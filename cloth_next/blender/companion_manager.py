# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned companion process and strict production readiness handshake."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import bpy

from .. import manifest_version
from ..bake.companion_bundle import validate_bundle
from ..bake.controller import shared_controller
from ..bake.status import BakeJobKind, BakeState
from ..bake.transport import (BakeWindowReady, EnterBakeMode,
                              LocalSocketServer)
from . import modal_lock

STARTUP_TIMEOUT_SECONDS = 7.0
_TERMINAL_GRACE = {BakeState.FINISHED: 1.5, BakeState.CANCELLED: 1.0}

_process = None
_server = None
_unsubscribe = None
_owned_for_attempt = False
_pending_request: EnterBakeMode | None = None
_pending_deadline: float | None = None
_ready: BakeWindowReady | None = None
_startup_error = ""
_terminal_deadline = None
_force_deadline = None
_kill_deadline = None
_production_session = False
_production_job_id = ""
_last_error_key: tuple[str, str, float] | None = None


def _log_path() -> Path:
    try:
        root = Path(bpy.utils.user_resource("CONFIG"))
    except (AttributeError, TypeError):
        root = Path(bpy.app.tempdir)
    path = root / "cloth_next" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path / "companion-startup.log"


def log_directory() -> Path:
    return _log_path().parent


def _error_log_path() -> Path:
    return log_directory() / "bake-errors.log"


def _log(stage: str, message: str, **details) -> None:
    """Bounded Blender-side startup diagnostics without private UI paths."""
    try:
        path = _log_path()
        if path.exists() and path.stat().st_size > 256 * 1024:
            backup = path.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            path.replace(backup)
        record = {"time": time.time(), "stage": stage, "message": message,
                  **details}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def _persist_bake_error(snapshot) -> None:
    """Persist one complete local diagnostic record per terminal failure."""
    global _last_error_key
    key = (str(getattr(snapshot, "job_id", "")),
           str(getattr(snapshot, "error_code", "") or "CNX-E199"),
           float(getattr(snapshot, "updated_at", 0.0) or 0.0))
    if key == _last_error_key:
        return
    try:
        path = _error_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 1024 * 1024:
            backup = path.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            path.replace(backup)
        record = {
            "time": time.time(), "job_id": key[0], "error_code": key[1],
            "state": str(getattr(getattr(snapshot, "state", ""),
                                 "value", getattr(snapshot, "state", ""))),
            "activity": str(getattr(getattr(snapshot, "activity_code", ""),
                                    "value", getattr(snapshot, "activity_code", ""))),
            "stage": str(getattr(snapshot, "activity_detail", ""))[:1024],
            "summary": str(getattr(snapshot, "error_summary", ""))[:8192],
            "details": str(getattr(snapshot, "error_details", ""))[:65536],
        }
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, sort_keys=True,
                                    ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _last_error_key = key
    except (OSError, TypeError, ValueError):
        # Error reporting must never replace or mask the original Bake error.
        pass


def running() -> bool:
    return _process is not None and _process.poll() is None


def _publish(snapshot) -> None:
    global _terminal_deadline
    if snapshot.state is BakeState.ERROR:
        _persist_bake_error(snapshot)
    if _server is not None:
        try:
            _server.publish(snapshot)
        except (OSError, TypeError, ValueError) as exc:
            _log("transport", "Bake status publication failed",
                 error_type=type(exc).__name__, error=str(exc)[:2048])
    if (_production_session and snapshot.job_kind is BakeJobKind.BAKE
            and snapshot.state is BakeState.ERROR):
        modal_lock.release(snapshot.job_id)
        _log("error","Companion remains open for user acknowledgement",
             job_id=snapshot.job_id,
             error_code=getattr(snapshot,"error_code","") or "CNX-E199")
        return
    if (_production_session and snapshot.job_kind is BakeJobKind.BAKE
            and snapshot.state in _TERMINAL_GRACE
            and _terminal_deadline is None):
        modal_lock.release(snapshot.job_id)
        _terminal_deadline = time.monotonic() + _TERMINAL_GRACE[snapshot.state]
        _log("shutdown", "terminal Bake state scheduled Companion exit",
             job_id=snapshot.job_id, requested_job_id=_production_job_id,
             state=snapshot.state.value,
             grace_seconds=_TERMINAL_GRACE[snapshot.state])


def _launch() -> tuple[bool, str, bool]:
    """Launch or reuse. Success means process creation, never readiness."""
    global _process, _server, _unsubscribe
    if running() and _server is not None:
        _log("process", "reusing running companion")
        return True, "Bake window process reused", False
    if _process is not None or _server is not None or _unsubscribe is not None:
        shutdown()
    extension_root = Path(__file__).resolve().parents[1]
    root = extension_root.parent
    python = os.environ.get("CLOTH_NEXT_COMPANION_PYTHON", sys.executable)
    try:
        command = [str(validate_bundle(extension_root, manifest_version()))]
        category = "bundled"
    except (OSError, ValueError, KeyError) as exc:
        if (os.environ.get("CLOTH_NEXT_DEVELOPER_COMPANION") == "1"
                and (root / "companion/app.py").is_file()):
            command = [python, "-m", "companion.app"]
            category = "developer"
        else:
            message = f"Companion manifest validation failed: {exc}"
            _log("manifest", message)
            return False, message, False
    try:
        _server = LocalSocketServer()
    except OSError as exc:
        message = f"Bake window transport connection failed: {exc}"
        _log("transport", message)
        return False, message, False
    command += ["--port", str(_server.port), "--token", _server.token]
    try:
        _process = subprocess.Popen(command, cwd=root, shell=False)
    except OSError as exc:
        _server.close(); _server = None
        message = f"Bake executable was not found or could not start: {exc}"
        _log("process", message, executable_category=category)
        return False, message, False
    _unsubscribe = shared_controller.subscribe(_publish)
    _server.publish(shared_controller.snapshot())
    _log("process", "companion process created", pid=_process.pid,
         executable_category=category, version=manifest_version())
    if not bpy.app.timers.is_registered(_pulse):
        bpy.app.timers.register(_pulse, first_interval=.05)
    return True, "Bake window process created", True


def begin_bake_mode(request: EnterBakeMode) -> tuple[bool, str]:
    """Reserve one readiness attempt. No worker, PPF, or modal lock starts."""
    global _pending_request, _pending_deadline, _ready, _startup_error
    global _owned_for_attempt, _production_session, _production_job_id
    if _pending_request is not None:
        return False, "A Bake window startup is already pending."
    ok, message, owned = _launch()
    if not ok:
        _startup_error = message
        return False, message
    _pending_request = request
    _pending_deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    _ready = None
    _startup_error = ""
    _owned_for_attempt = owned
    # This request is the authoritative production-session boundary. Do not
    # infer ownership later from a transient modal-lock observation: the
    # terminal transition can release that lock before a listener sees it.
    _production_session = True
    _production_job_id = request.job_id
    _log("handshake", "waiting for Bake window readiness",
         job_id=request.job_id, frame_start=request.frame_start,
         frame_end=request.frame_end)
    # A reused, already-connected companion does not send another `ready`.
    if getattr(_server, "connected", lambda: False)():
        _server.enter_bake_mode(request)
    return True, message


def launch() -> tuple[bool, str]:
    """Manual/diagnostic launch compatibility; not a readiness assertion."""
    ok, message, _owned = _launch()
    return ok, message


def ensure_running() -> tuple[bool, str]:
    if running(): return True, "Bake window reused"
    if _process is not None or _server is not None or _unsubscribe is not None:
        shutdown()
    return launch()


def startup_status(job_id: str) -> tuple[str, str]:
    if _ready is not None and _ready.job_id == job_id:
        return "READY", "Bake window ready"
    if _pending_request is not None and _pending_request.job_id == job_id:
        return "WAITING", "Opening Bake window…"
    if _startup_error:
        return "ERROR", _startup_error
    return "CANCELLED", "Bake window startup is no longer active."


def consume_ready(job_id: str) -> bool:
    global _pending_request, _pending_deadline
    if _ready is None or _ready.job_id != job_id or not _ready.ready:
        return False
    _pending_request = None
    _pending_deadline = None
    return True


def cancel_startup(job_id: str, reason: str = "Bake window startup cancelled.") -> None:
    global _pending_request, _pending_deadline, _ready, _startup_error
    if _pending_request is None or _pending_request.job_id != job_id:
        return
    _log("handshake", reason, job_id=job_id)
    _pending_request = None; _pending_deadline = None; _ready = None
    _startup_error = reason
    modal_lock.release(job_id)
    if _owned_for_attempt:
        _request_close_now()


def _handle_ready(payload: dict) -> None:
    global _ready, _startup_error
    try:
        ack = BakeWindowReady(**payload)
    except (TypeError, ValueError) as exc:
        _startup_error = f"Malformed Bake window readiness acknowledgement: {exc}"
        return
    request = _pending_request
    if request is None or ack.job_id != request.job_id:
        _log("handshake", "ignored stale readiness", job_id=ack.job_id)
        return
    if not ack.ready:
        missing = []
        if not ack.window_visible: missing.append("visible")
        if not ack.topmost_applied: missing.append("topmost")
        if not ack.transport_ready: missing.append("transport")
        _startup_error = "Bake window readiness failed: " + ", ".join(missing)
        cancel_startup(request.job_id, _startup_error)
        return
    _ready = ack
    _log("handshake", "Bake window ready", job_id=ack.job_id,
         companion_pid=ack.companion_process_id)


def _pulse():
    global _pending_request, _pending_deadline, _startup_error
    global _terminal_deadline, _force_deadline, _kill_deadline
    if _server is None:
        return None
    message = _server.poll_request()
    if message:
        kind = message["type"] if isinstance(message, dict) else message
        if kind == "ready" and _pending_request is not None:
            _server.enter_bake_mode(_pending_request)
            _log("transport", "companion transport ready",
                 job_id=_pending_request.job_id)
        elif kind == "bake_window_ready":
            _handle_ready(message["payload"])
        elif kind == "startup_error" and _pending_request is not None:
            cancel_startup(_pending_request.job_id,
                           str(message["payload"].get("message",
                               "Bake window startup failed.")))
        elif kind == "cancel_request":
            snapshot = shared_controller.snapshot()
            if _pending_request is not None:
                cancel_startup(_pending_request.job_id)
            elif snapshot.can_cancel:
                shared_controller.request_cancel()
        elif kind == "close_notice":
            snapshot = shared_controller.snapshot()
            modal_lock.release(snapshot.job_id)
            if snapshot.active:
                shared_controller.update(
                    status_message="Bake window closed unexpectedly.")
            else:
                _log("shutdown", "Companion acknowledged close",
                     job_id=snapshot.job_id, state=snapshot.state.value)
    now = time.monotonic()
    if _pending_request is not None:
        if not running():
            cancel_startup(_pending_request.job_id,
                           "Bake window process exited during startup.")
        elif _pending_deadline is not None and now >= _pending_deadline:
            cancel_startup(_pending_request.job_id,
                           "The Bake window did not become ready. Bake was not started.")
    if _terminal_deadline is not None and now >= _terminal_deadline:
        _log("shutdown", "requesting graceful Companion exit",
             job_id=_production_job_id)
        _server.shutdown_companion(); _terminal_deadline = None
        _force_deadline = now + 1.0
    if _force_deadline is not None:
        if not running(): _dispose_transport(); return None
        if now >= _force_deadline:
            _log("shutdown", "terminating unresponsive Companion",
                 job_id=_production_job_id, pid=getattr(_process, "pid", None))
            _process.terminate(); _force_deadline = None; _kill_deadline = now + 1.0
    if _kill_deadline is not None:
        if not running(): _dispose_transport(); return None
        if now >= _kill_deadline:
            _log("shutdown", "killing unresponsive Companion",
                 job_id=_production_job_id, pid=getattr(_process, "pid", None))
            _process.kill(); _dispose_transport(); return None
    if _production_session and not running():
        snapshot = shared_controller.snapshot()
        modal_lock.release(snapshot.job_id)
        if snapshot.active:
            shared_controller.update(status_message="Bake window closed unexpectedly.")
        _dispose_transport(); return None
    return .05


def _request_close_now() -> None:
    global _force_deadline
    if _server is not None:
        _server.shutdown_companion()
    _force_deadline = time.monotonic() + 1.0


def _dispose_transport() -> None:
    global _process, _server, _unsubscribe, _terminal_deadline
    global _force_deadline, _kill_deadline, _production_session
    global _production_job_id, _owned_for_attempt
    if bpy.app.timers.is_registered(_pulse): bpy.app.timers.unregister(_pulse)
    if _unsubscribe: _unsubscribe(); _unsubscribe = None
    if _server: _server.close(join=False)
    _process = None; _server = None; _terminal_deadline = None
    _force_deadline = None; _kill_deadline = None; _production_session = False
    _production_job_id = ""; _owned_for_attempt = False


def shutdown() -> None:
    global _process, _server, _unsubscribe, _pending_request, _ready
    global _production_session, _production_job_id, _owned_for_attempt
    global _terminal_deadline, _force_deadline, _kill_deadline
    global _pending_deadline, _startup_error
    modal_lock.release()
    _pending_request = None; _ready = None
    if bpy.app.timers.is_registered(_pulse): bpy.app.timers.unregister(_pulse)
    if _unsubscribe: _unsubscribe(); _unsubscribe = None
    if _server: _server.shutdown_companion()
    process = _process
    if process:
        try: process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=1)
    if _server: _server.close()
    _process = None; _server = None
    _production_session = False; _production_job_id = ""
    _owned_for_attempt = False
    _terminal_deadline = None; _force_deadline = None; _kill_deadline = None
    _pending_deadline = None; _startup_error = ""
