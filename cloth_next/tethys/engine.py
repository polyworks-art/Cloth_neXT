# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bundled native TETHYS Engine: discovery, availability and one owned process.

The engine ships inside the extension at ``bin/<platform filename>`` and is
authenticated by ``tethys_manifest.json`` (size and SHA-256), the same way
as the Bake companion. GAIA availability is decided by the caller from the
existing solver selection and GAIA access; this module never launches the
engine on its own initiative.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import threading
import time

from ..platform_support import PlatformSpec, platform_spec
from .protocol import PROTOCOL_VERSION

MANIFEST = "tethys_manifest.json"
FILENAMES = {"windows-x64": "TETHYS Engine.exe", "linux-x64": "tethys-engine"}
DEVELOPER_OVERRIDE = "CLOTH_NEXT_TETHYS_ENGINE"
EVENT_PREFIX = "TETHYS "
MAX_EVENT_BYTES = 64 * 1024


class EngineUnavailable(RuntimeError):
    """The bundled engine is missing, damaged or incompatible."""


def extension_root() -> Path:
    return Path(__file__).resolve().parents[1]


def engine_filename(platform: PlatformSpec | None = None) -> str:
    return FILENAMES[(platform or platform_spec()).blender_platform]


def locate(root: Path | None = None, *, platform: PlatformSpec | None = None) -> Path:
    """Authenticated engine path. Raises EngineUnavailable with a clear reason."""
    override = os.environ.get(DEVELOPER_OVERRIDE, "").strip()
    if override and root is None:
        path = Path(override)
        if not path.is_file():
            raise EngineUnavailable(f"{DEVELOPER_OVERRIDE} does not point to a file")
        return path
    selected = platform or platform_spec()
    root = Path(root) if root is not None else extension_root()
    manifest = root / MANIFEST
    try:
        payload = json.loads(manifest.read_text("utf-8"))
    except FileNotFoundError:
        raise EngineUnavailable("The TETHYS Engine is not included in this Cloth NeXt build") from None
    except (OSError, ValueError) as exc:
        raise EngineUnavailable("The TETHYS Engine manifest is damaged. Reinstall Cloth NeXt") from exc
    required = {"schema_version", "filename", "platform", "file_size", "sha256", "protocol"}
    if (not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != 1
            or payload["filename"] != engine_filename(selected)
            or payload["platform"] != selected.blender_platform):
        raise EngineUnavailable("The TETHYS Engine does not match this platform. Reinstall Cloth NeXt")
    if payload["protocol"] != PROTOCOL_VERSION:
        raise EngineUnavailable("The TETHYS Engine protocol does not match this add-on. Reinstall Cloth NeXt")
    binary = root / "bin" / payload["filename"]
    try:
        data = binary.read_bytes()
    except OSError:
        raise EngineUnavailable("The TETHYS Engine file is missing. Reinstall Cloth NeXt") from None
    if len(data) != payload["file_size"] or hashlib.sha256(data).hexdigest() != payload["sha256"]:
        raise EngineUnavailable("The TETHYS Engine file is damaged. Reinstall Cloth NeXt")
    if selected.executable_permission_required and os.name == "posix" and not binary.stat().st_mode & 0o111:
        # Blender's ZIP installer drops mode bits; restore only after authentication.
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return binary


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""


def gaia_availability(registry, access_granted: bool) -> Availability:
    """TETHYS may start only with a healthy selected GAIA build and GAIA access."""
    # TETHYS requires GAIA. Mother knows best.
    from ..updater import engines
    selected = getattr(registry, "selected", None)
    if selected is None or engines.engine_of(selected) != engines.GAIA:
        return Availability(False, "Select GAIA as the solver engine in Preferences")
    if not engines._selectable(selected):
        return Availability(False, "The selected GAIA installation is not healthy. Check Preferences")
    if not access_granted:
        return Availability(False, "Needs GAIA access")
    return Availability(True)


def _creation_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def probe(executable: Path, *, timeout: float = 10.0) -> str:
    """Run ``--version``; returns the engine version line or raises EngineUnavailable."""
    try:
        result = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                                timeout=timeout, creationflags=_creation_flags())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineUnavailable(f"The TETHYS Engine could not start ({exc.__class__.__name__})") from exc
    line = result.stdout.strip()
    if result.returncode != 0 or not line.startswith("TETHYS Engine ") or \
            not line.endswith(f"protocol {PROTOCOL_VERSION}"):
        raise EngineUnavailable("The TETHYS Engine reported an incompatible version")
    return line


class EngineJob:
    """One owned engine process; events are read on a daemon thread.

    The process is placed in the same kill-on-close ownership boundary used
    for solver processes, so closing Blender or the job never leaves an
    engine running.
    """

    def __init__(self, executable: Path, request_path: Path, cancel_path: Path):
        from ..ppf.process import _PosixProcessGroup, _WindowsJob
        self.cancel_path = Path(cancel_path)
        self.events: queue.Queue = queue.Queue()
        self.stderr_tail: list[str] = []
        self._owner = _WindowsJob() if sys.platform == "win32" else _PosixProcessGroup()
        kwargs = {"creationflags": _creation_flags()}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(
            [str(executable), "run", str(request_path)], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        try:
            self._owner.assign(self.process)
        except Exception:
            self.process.kill()
            self.process.wait()
            self._owner.close()
            raise
        self._reader = threading.Thread(target=self._read, name="TETHYS events", daemon=True)
        self._reader.start()
        self._errors = threading.Thread(target=self._read_errors, name="TETHYS stderr", daemon=True)
        self._errors.start()

    def _read(self):
        for raw in self.process.stdout:
            if len(raw) > MAX_EVENT_BYTES:
                continue
            text = raw.decode("utf-8", "replace").strip()
            if not text.startswith(EVENT_PREFIX):
                continue
            try:
                event = json.loads(text[len(EVENT_PREFIX):])
            except ValueError:
                continue
            if isinstance(event, dict) and isinstance(event.get("event"), str):
                self.events.put(event)

    def _read_errors(self):
        for raw in self.process.stderr:
            self.stderr_tail.append(raw.decode("utf-8", "replace").rstrip())
            del self.stderr_tail[:-20]

    def poll(self):
        return self.process.poll()

    def take_events(self) -> list[dict]:
        items = []
        while True:
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                return items

    def cancel(self, *, grace: float = 5.0) -> None:
        """Ask the engine to stop at the next frame; terminate it after `grace` seconds."""
        try:
            self.cancel_path.write_bytes(b"cancel")
        except OSError:
            pass
        deadline = time.monotonic() + grace
        while self.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if self.process.poll() is None:
            self.process.kill()
        self.close()

    def close(self) -> None:
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self._reader.join(timeout=2)
        self._errors.join(timeout=2)
        self._owner.close()
        for stream in (self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except (OSError, AttributeError):
                pass
