# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Background diagnostics: collect performance, bake results, errors and crashes,
and send them in packages while the user allows it. Never imports ``bpy``.

One daemon thread per Blender session. Switched off, nothing is collected,
stored or sent. Nothing is sent while a Bake runs. A signed-in account's
token is only ever placed in the request header (see ``transport``).
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from . import crashes, payload, store
from .sanitize import scrub
from .transport import Outcome, post_package

SAMPLE_SECONDS = 60.0
FLUSH_SECONDS = 15 * 60.0
FIRST_FLUSH_SECONDS = 2 * 60.0
MAX_BACKOFF_SECONDS = 6 * 3600.0
TICK_SECONDS = 5.0
MAX_ERRORS_PER_SESSION = 200
MAX_PACKAGES_PER_FLUSH = 5
_CODE = re.compile(r"\bCNX-E\d{3,4}\b")
_MB = 1024 * 1024


def _logger_name(name: str) -> str:
    """``bl_ext.user_default.cloth_next.bake`` -> ``cloth_next.bake``."""
    head, sep, tail = str(name).partition("cloth_next")
    return (sep + tail)[:96] if sep else str(name)[:96]


def _mb(value) -> int | None:
    return None if value is None else int(round(value / _MB))


def _pct(value) -> float | None:
    return None if value is None else round(float(value), 1)


class DiagnosticsService:
    def __init__(self, root: Path, *, environment: dict, origin: str | None = None,
                 sender: Callable[..., Outcome] | None = None,
                 token_provider: Callable[[], str | None] | None = None,
                 system_provider=None, gpu_provider=None,
                 telemetry_snapshot: Callable[[], object] | None = None,
                 bake_active: Callable[[], bool] = lambda: False,
                 clock: Callable[[], float] = time.monotonic,
                 sample_seconds: float = SAMPLE_SECONDS, flush_seconds: float = FLUSH_SECONDS,
                 first_flush_seconds: float = FIRST_FLUSH_SECONDS, tick_seconds: float = TICK_SECONDS):
        self.root = Path(root)
        self.queue = store.EventQueue(self.root / "queue.jsonl")
        self.session_id = str(uuid.uuid4())
        self._environment = {key: environment.get(key, "") for key in payload.ENVIRONMENT_FIELDS}
        self._origin = origin
        self._sender = sender or (lambda origin, package, token=None: post_package(
            origin, package, token=token,
            user_agent=f"ClothNeXt-Diagnostics/{self._environment['addon_version']}"))
        self._token_provider = token_provider
        self._system = system_provider
        self._gpu = gpu_provider
        self._gpu_failures = 0
        self._telemetry_snapshot = telemetry_snapshot
        self._bake_active = bake_active
        self._clock = clock
        self._sample_every = float(sample_seconds)
        self._flush_every = float(flush_seconds)
        self._tick = float(tick_seconds)
        self._lock = threading.RLock()
        self._pending: list[dict] = []
        self._errors_this_session = 0
        self._consent = threading.Event()
        self._online = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        now = clock()
        self._next_sample = now
        self._next_flush = now + float(first_flush_seconds)
        self._failures = 0
        self.last_outcome: Outcome | None = None

    # ----- settings (any thread) -------------------------------------------------
    @property
    def consent(self) -> bool:
        return self._consent.is_set()

    def set_consent(self, allowed: bool) -> None:
        """Turning it off deletes everything collected so far."""
        if allowed:
            self._consent.set()
            return
        self._consent.clear()
        with self._lock:
            self._pending.clear()
        self.queue.clear()
        store.forget_fallback_id(self.root)

    def set_online(self, allowed: bool) -> None:
        (self._online.set if allowed else self._online.clear)()

    def set_gpu_name(self, name: str) -> None:
        if name and not self._environment.get("gpu"):
            self._environment["gpu"] = scrub(name, payload.ENVIRONMENT_FIELDS["gpu"])

    # ----- recording (any thread, cheap, never raises) ---------------------------
    def _add(self, event: dict) -> None:
        if not self._consent.is_set():
            return
        event.setdefault("at", payload.utc_now())
        with self._lock:
            self._pending.append(event)

    def record_error(self, logger: str, level: str, message: str) -> None:
        if not self._consent.is_set():
            return
        with self._lock:
            if self._errors_this_session >= MAX_ERRORS_PER_SESSION:
                return
            self._errors_this_session += 1
        text = scrub(message, payload.MESSAGE_LIMIT)
        match = _CODE.search(str(message))
        self._add({"type": "error", "code": match.group(0) if match else None,
                   "logger": _logger_name(logger), "level": str(level)[:16], "message": text})

    def record_crash(self, source: str, kind: str, trace: str = "") -> None:
        self._add({"type": "crash", "source": str(source)[:32], "kind": str(kind)[:32],
                   "trace": scrub(trace, payload.TRACE_LIMIT) if trace else None})

    def record_bake(self, snapshot) -> None:
        """A finished, failed or cancelled Bake (a ``BakeSnapshot``)."""
        try:
            frames = int(getattr(snapshot, "progress_current", 0) or 0)
            seconds = float(getattr(snapshot, "elapsed_seconds", 0.0) or 0.0)
            state = getattr(snapshot, "state", "")
            self._add({"type": "bake",
                       "engine": scrub(getattr(snapshot, "solver_name", ""), 64) or None,
                       "engine_version": scrub(getattr(snapshot, "solver_version", ""), 64) or None,
                       "backend": scrub(getattr(snapshot, "solver_backend", ""), 32) or None,
                       "execution": str(getattr(snapshot, "execution", "") or "")[:16] or None,
                       "result": str(getattr(state, "value", state))[:32],
                       "error_code": str(getattr(snapshot, "error_code", "") or "")[:32] or None,
                       "frames": frames,
                       "seconds": round(seconds, 1),
                       "seconds_per_frame": round(seconds / frames, 3) if frames > 0 else None})
        except Exception:  # noqa: BLE001 - diagnostics never break a Bake
            pass

    def collect_previous_session(self, marker: store.SessionMarker,
                                 python_crash_path: Path | None = None,
                                 companion_crash_path: Path | None = None) -> None:
        """Run once at start, before ``crash_trace.enable()`` empties its file."""
        unclean = marker.begin(self.session_id)
        trace = crashes.previous_python_crash(python_crash_path)
        if trace or unclean:
            self.record_crash("blender", "python_fatal" if trace else "unclean_exit", trace)
        state = store.read_state(self.root)
        path = companion_crash_path or crashes.companion_crash_path()
        if "companion_offset" not in state:
            # First run: older crashes predate the consent and stay local.
            try:
                state["companion_offset"] = path.stat().st_size
            except OSError:
                state["companion_offset"] = 0
        else:
            found, state["companion_offset"] = crashes.new_companion_crashes(
                path, int(state.get("companion_offset") or 0))
            for crash in found:
                self.record_crash("companion", crash["mode"] or "fatal", crash["trace"])
        store.write_state(self.root, state)

    # ----- what would be sent ----------------------------------------------------
    def identity(self) -> dict:
        return {"hardware_id": store.hardware_id(self.root), "session_id": self.session_id,
                **self._environment}

    def preview(self) -> dict:
        with self._lock:
            pending = list(self._pending)
        return payload.build_package(self.identity(), self.queue.peek(1000) + pending)[0]

    # ----- thread ----------------------------------------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="clothnext-diagnostics",
                                            daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 3.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._thread = None
        try:
            self._persist_pending()
        except Exception:  # noqa: BLE001
            pass
        return stopped

    def _run(self) -> None:
        while not self._stop.wait(self._tick):
            try:
                self.step()
            except Exception:  # noqa: BLE001 - the thread must survive any failure
                logging.getLogger(__name__).debug("diagnostics step failed", exc_info=True)

    def step(self) -> None:
        """One iteration; public so tests can drive it without the thread."""
        if not self._consent.is_set():
            with self._lock:
                self._pending.clear()
            return
        now = self._clock()
        if now >= self._next_sample:
            self._next_sample = now + self._sample_every
            self._sample()
        self._persist_pending()
        if (now >= self._next_flush and self._online.is_set() and self._origin
                and not self._safe_bake_active()):
            self._flush(now)

    def _safe_bake_active(self) -> bool:
        try:
            return bool(self._bake_active())
        except Exception:  # noqa: BLE001
            return True

    def _persist_pending(self) -> None:
        with self._lock:
            events, self._pending = self._pending, []
        if events and self._consent.is_set():
            self.queue.append(events)

    def _sample(self) -> None:
        event: dict = {"type": "perf", "bake_active": self._safe_bake_active()}
        if self._system is not None:
            try:
                cpu, used, total = self._system.sample()
                event.update(cpu_percent=_pct(cpu), ram_used_mb=_mb(used), ram_total_mb=_mb(total))
            except Exception:  # noqa: BLE001
                pass
        gpu = self._gpu_sample(event["bake_active"])
        if gpu is not None:
            self.set_gpu_name(getattr(gpu, "name", ""))
            event.update(gpu_percent=_pct(gpu.utilization_percent),
                         vram_used_mb=_mb(gpu.vram_used_bytes),
                         vram_total_mb=_mb(gpu.vram_total_bytes))
        self._add(event)

    def _gpu_sample(self, bake_active: bool):
        if bake_active and self._telemetry_snapshot is not None:
            try:
                snapshot = self._telemetry_snapshot()
                if snapshot is not None and not snapshot.stale and snapshot.primary_gpu is not None:
                    return snapshot.primary_gpu
            except Exception:  # noqa: BLE001
                pass
        if self._gpu is None or self._gpu_failures >= 3:
            return None
        try:
            gpus = self._gpu()
            self._gpu_failures = 0
        except Exception:  # noqa: BLE001 - no NVIDIA GPU or driver
            self._gpu_failures += 1
            return None
        return max(gpus, key=lambda g: g.vram_used_bytes or -1, default=None)

    def _flush(self, now: float) -> None:
        identity = self.identity()
        token = None
        if self._token_provider is not None:
            try:
                token = self._token_provider() or None
            except Exception:  # noqa: BLE001 - no account: send anonymously
                token = None
        for _ in range(MAX_PACKAGES_PER_FLUSH):
            events = self.queue.peek(1000)
            if not events:
                break
            package, consumed = payload.build_package(identity, events)
            if not package["events"]:
                self.queue.drop(consumed)
                continue
            outcome = self._sender(self._origin, package, token=token)
            self.last_outcome = outcome
            if outcome.kind == "retry":
                self._failures += 1
                delay = outcome.retry_after
                if delay is None:
                    delay = min(MAX_BACKOFF_SECONDS, self._flush_every * (2 ** (self._failures - 1)))
                self._next_flush = now + min(MAX_BACKOFF_SECONDS, max(delay, 60.0))
                return
            self.queue.drop(consumed)  # sent, or refused for good: never resend it
            self._failures = 0
        self._next_flush = now + self._flush_every


class DiagnosticsLogHandler(logging.Handler):
    """Forwards WARNING and above of the Cloth NeXt loggers to the service."""

    def __init__(self, service: DiagnosticsService):
        super().__init__(level=logging.WARNING)
        self.service = service

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(__name__.rpartition(".")[0]):
                return  # never report the diagnostics module itself
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                error = record.exc_info[1]
                message = f"{message} | {type(error).__name__}: {error}"
            self.service.record_error(record.name, record.levelname, message)
        except Exception:  # noqa: BLE001 - logging must never fail
            pass
