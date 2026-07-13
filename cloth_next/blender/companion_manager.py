# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicitly owned Bake companion process, separate from PPF ownership."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import bpy
from .. import manifest_version
from ..bake.companion_bundle import validate_bundle
from ..bake.controller import shared_controller
from ..bake.transport import LocalSocketServer

_process=None; _server=None; _unsubscribe=None

def _pulse():
    if _server is None: return None
    request=_server.poll_request()
    if request=="cancel_request" and shared_controller.snapshot().can_cancel:
        shared_controller.request_cancel()
    return .1

def running(): return _process is not None and _process.poll() is None

def launch():
    global _process,_server,_unsubscribe
    if running(): return False, "Bake window is already running"
    extension_root=Path(__file__).resolve().parents[1]
    root=extension_root.parent
    python=os.environ.get("CLOTH_NEXT_COMPANION_PYTHON", sys.executable)
    try: command=[str(validate_bundle(extension_root,manifest_version()))]
    except (OSError,ValueError,KeyError):
        if os.environ.get("CLOTH_NEXT_DEVELOPER_COMPANION")=="1" and (root/"companion/app.py").is_file():
            command=[python,"-m","companion.app"]
        else: return False,"Bundled Bake companion is missing or failed validation"
    _server=LocalSocketServer()
    command += ["--port",str(_server.port),"--token",_server.token]
    try:
        _process=subprocess.Popen(command,cwd=root,shell=False)
    except OSError as exc:
        _server.close(); _server=None; return False,f"Could not launch Bake window: {exc}"
    _unsubscribe=shared_controller.subscribe(_server.publish)
    _server.publish(shared_controller.snapshot())
    if not bpy.app.timers.is_registered(_pulse): bpy.app.timers.register(_pulse,first_interval=.1)
    return True,"Bake window launched"

def ensure_running():
    if running(): return True, "Bake window reused"
    # A previous window may have exited while its socket/subscription/timer
    # remained until Blender's next lifecycle callback. Dispose that stale
    # session before creating the replacement so repeated bakes can never
    # accumulate transports or callbacks.
    if _process is not None or _server is not None or _unsubscribe is not None:
        shutdown()
    return launch()

def shutdown():
    global _process,_server,_unsubscribe
    if bpy.app.timers.is_registered(_pulse): bpy.app.timers.unregister(_pulse)
    if _unsubscribe: _unsubscribe(); _unsubscribe=None
    if _server: _server.shutdown_companion()
    process=_process
    if process:
        try: process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=1)
    if _server: _server.close()
    _process=None; _server=None
