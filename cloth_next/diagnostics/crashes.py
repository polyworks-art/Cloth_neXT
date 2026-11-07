# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find crashes of the previous session in the existing local crash logs.

* ``%TEMP%/cloth_next_python_crash.txt``: written by ``blender/crash_trace``
  (Dev builds). It is emptied at every Blender start, so it must be read
  before ``crash_trace.enable()`` runs.
* ``companion-crash.log``: faulthandler output of the Bake window and the
  account window, one ``--- <time> pid=<pid> mode=<mode>`` section per start.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PYTHON_CRASH_FILE = "cloth_next_python_crash.txt"
MAX_READ_BYTES = 256 * 1024
_FATAL_MARKERS = ("Fatal Python error", "fatal exception", "Current thread", "Thread 0x")


def python_crash_path() -> Path:
    return Path(tempfile.gettempdir()) / PYTHON_CRASH_FILE


def companion_crash_path(environ=None) -> Path:
    env = os.environ if environ is None else environ
    if sys.platform == "win32":
        return Path(env.get("LOCALAPPDATA", Path.home())) / "Cloth NeXt" / "logs" / "companion-crash.log"
    return (Path(env.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
            / "ClothNeXt" / "logs" / "companion-crash.log")


def _looks_fatal(text: str) -> bool:
    return any(marker in text for marker in _FATAL_MARKERS)


def previous_python_crash(path: Path | None = None) -> str:
    """The Python stack from the last crash, or "" when the last session had none."""
    path = path or python_crash_path()
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(MAX_READ_BYTES)
    except OSError:
        return ""
    body = text.split("\n", 1)[1] if "\n" in text else ""
    return body.strip() if _looks_fatal(body) else ""


def new_companion_crashes(path: Path | None, offset: int) -> tuple[list[dict], int]:
    """Crashed sections written after ``offset``; returns them and the new offset.

    A section counts as a crash when faulthandler wrote a stack into it. A
    rotated (smaller) file is read from the start.
    """
    path = path or companion_crash_path()
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    start = offset if 0 <= offset <= size else 0
    if size - start > MAX_READ_BYTES:
        start = size - MAX_READ_BYTES
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return [], offset
    crashes = []
    for section in text.split("\n--- "):
        header, _, body = section.partition("\n")
        mode = ""
        for part in header.split():
            if part.startswith("mode="):
                mode = part[5:]
        if body.strip() and _looks_fatal(body):
            crashes.append({"mode": mode, "trace": body.strip()})
    return crashes, size
