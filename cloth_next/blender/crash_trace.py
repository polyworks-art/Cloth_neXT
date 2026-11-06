# SPDX-License-Identifier: GPL-3.0-or-later
"""Dev builds: write the Python stack of every thread when Blender crashes.

Blender's own crash report (``%TEMP%/<file>.crash.txt``) sometimes has no
"# Python backtrace" section, and python313.dll ships without symbols, so a
crash inside a timer only shows ``py_timer_execute`` and anonymous
interpreter frames. :mod:`faulthandler` adds the missing piece: on an access
violation it writes the Python traceback of all threads to
``%TEMP%/cloth_next_python_crash.txt`` and then lets Blender's handler run.

Only Dev builds enable it (stable builds stay untouched). The file is
overwritten at every Blender start, so it never grows across sessions.
"""

from __future__ import annotations

import faulthandler
import logging
import sys
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)
FILE_NAME = "cloth_next_python_crash.txt"
_stream = None


def path() -> Path:
    return Path(tempfile.gettempdir()) / FILE_NAME


def enable(dev_build: bool | None = None) -> bool:
    """Start tracing; returns True when this call enabled it."""
    global _stream
    if dev_build is None:
        from ..developer import is_dev_build
        dev_build = is_dev_build()
    if not dev_build or _stream is not None or faulthandler.is_enabled():
        return False
    try:
        stream = open(path(), "w", encoding="utf-8")  # noqa: SIM115 - kept open on purpose
        stream.write(f"Cloth NeXt Python crash trace, Python {sys.version.split()[0]}\n")
        stream.flush()
        faulthandler.enable(file=stream, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        _log.debug("Python crash trace not available", exc_info=True)
        return False
    _stream = stream
    return True


def disable() -> None:
    global _stream
    if _stream is None:
        return
    try:
        faulthandler.disable()
    finally:
        try:
            _stream.close()
        except OSError:
            pass
        _stream = None
