# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local files of the diagnostics upload: event buffer, hardware id, session marker.

Nothing here contacts a server. The buffer is a bounded JSON-lines file; the
oldest events are dropped first when it grows past its size or age limit.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

MAX_QUEUE_BYTES = 2 * 1024 * 1024
MAX_EVENT_AGE_SECONDS = 7 * 24 * 3600


def diagnostics_root(environ=None) -> Path:
    env = os.environ if environ is None else environ
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "Cloth NeXt" / "diagnostics"
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ClothNeXt" / "diagnostics"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


class EventQueue:
    """Append-only buffer with ``peek``/``drop`` from the front. Thread-safe."""

    def __init__(self, path: Path, *, max_bytes: int = MAX_QUEUE_BYTES,
                 max_age_seconds: float = MAX_EVENT_AGE_SECONDS, clock=time.time):
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.max_age = float(max_age_seconds)
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        oldest = self._clock() - self.max_age
        records = []
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if (isinstance(record, dict) and isinstance(record.get("e"), dict)
                    and isinstance(record.get("t"), (int, float)) and record["t"] >= oldest):
                records.append(record)
        return records

    def _save(self, records: list[dict]) -> None:
        lines = [json.dumps(record, separators=(",", ":")) for record in records]
        total = sum(len(line) + 1 for line in lines)
        while lines and total > self.max_bytes:
            total -= len(lines.pop(0)) + 1
        if not lines:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)

    def append(self, events: list[dict]) -> None:
        if not events:
            return
        with self._lock:
            now = self._clock()
            self._save(self._load() + [{"t": now, "e": event} for event in events])

    def peek(self, limit: int) -> list[dict]:
        with self._lock:
            return [record["e"] for record in self._load()[: max(0, int(limit))]]

    def drop(self, count: int) -> None:
        with self._lock:
            self._save(self._load()[max(0, int(count)):])

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)

    def __len__(self) -> int:
        with self._lock:
            return len(self._load())


HARDWARE_ID_SALT = "cloth-next-diagnostics-v1:"


def _machine_guid() -> str:
    """The operating system's machine id (Windows MachineGuid, Linux machine-id), or ""."""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                value, _kind = winreg.QueryValueEx(key, "MachineGuid")
                return str(value).strip()
        except OSError:
            return ""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            value = Path(path).read_text(encoding="ascii").strip()
        except (OSError, ValueError):
            continue
        if value:
            return value
    return ""


def _fallback_id(root: Path) -> str:
    """Random id kept in the diagnostics folder when the machine id cannot be read."""
    path = Path(root) / "install.json"
    value = _read_json(path)
    if isinstance(value, dict):
        try:
            return str(uuid.UUID(str(value.get("install_id"))))
        except ValueError:
            pass
    new_id = str(uuid.uuid4())
    try:
        _write_json(path, {"install_id": new_id})
    except OSError:
        pass
    return new_id


def hardware_id(root: Path, machine_guid=_machine_guid) -> str:
    """Stable id of this computer (decided by Tim, 2026-11-06), sent for every package.

    Only a salted SHA-256 of the machine id leaves the computer, never the
    machine id itself, so it cannot be matched with other software's ids.
    """
    source = machine_guid() or "fallback:" + _fallback_id(root)
    return hashlib.sha256((HARDWARE_ID_SALT + source).encode("utf-8")).hexdigest()


def forget_fallback_id(root: Path) -> None:
    (Path(root) / "install.json").unlink(missing_ok=True)


class SessionMarker:
    """Tells whether the previous Blender session ended cleanly."""

    def __init__(self, root: Path):
        self.path = Path(root) / "session.json"

    def begin(self, session_id: str) -> dict | None:
        """Start a session; returns the previous session if it did not end cleanly."""
        previous = _read_json(self.path)
        try:
            _write_json(self.path, {"session_id": session_id, "clean": False,
                                    "started": time.time()})
        except OSError:
            pass
        if isinstance(previous, dict) and previous.get("clean") is False:
            return previous
        return None

    def end(self, session_id: str) -> None:
        try:
            _write_json(self.path, {"session_id": session_id, "clean": True,
                                    "ended": time.time()})
        except OSError:
            pass


def read_state(root: Path) -> dict:
    value = _read_json(Path(root) / "state.json")
    return value if isinstance(value, dict) else {}


def write_state(root: Path, state: dict) -> None:
    try:
        _write_json(Path(root) / "state.json", state)
    except OSError:
        pass
