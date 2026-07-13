# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicitly owned Bake companion process, separate from PPF ownership."""
from __future__ import annotations
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
from ..bake.transport import LocalSocketServer

_process=None; _server=None; _unsubscribe=None
_terminal_deadline=None; _force_deadline=None; _kill_deadline=None
_production_session=False

_TERMINAL_GRACE = {BakeState.FINISHED: 1.5, BakeState.CANCELLED: 1.0,
                   BakeState.ERROR: 2.5}

def _publish(snapshot):
    global _terminal_deadline, _production_session
    if _server is not None:
        _server.publish(snapshot)
    if snapshot.job_kind is BakeJobKind.BAKE and snapshot.active:
        _production_session=True
    if (_production_session and snapshot.state in _TERMINAL_GRACE
            and _terminal_deadline is None):
        _terminal_deadline=time.monotonic()+_TERMINAL_GRACE[snapshot.state]

def _pulse():
    global _terminal_deadline,_force_deadline,_kill_deadline,_process
    if _server is None: return None
    request=_server.poll_request()
    if request=="cancel_request" and shared_controller.snapshot().can_cancel:
        shared_controller.request_cancel()
    elif request=="close_notice" and shared_controller.snapshot().active:
        shared_controller.update(status_message="Bake window closed unexpectedly.")
    now=time.monotonic()
    if _terminal_deadline is not None and now >= _terminal_deadline:
        _server.shutdown_companion(); _terminal_deadline=None
        _force_deadline=now+1.0
    if _force_deadline is not None:
        if not running():
            _dispose_transport(); return None
        if now >= _force_deadline:
            _process.terminate(); _force_deadline=None; _kill_deadline=now+1.0
    if _kill_deadline is not None:
        if not running():
            _dispose_transport(); return None
        if now >= _kill_deadline:
            _process.kill(); _dispose_transport(); return None
    if _production_session and not running() and shared_controller.snapshot().active:
        shared_controller.update(status_message="Bake window closed unexpectedly.")
        _dispose_transport(); return None
    return .1

def running(): return _process is not None and _process.poll() is None

def launch():
    global _process,_server,_unsubscribe,_terminal_deadline,_force_deadline,_kill_deadline,_production_session
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
    _terminal_deadline=None; _force_deadline=None; _kill_deadline=None
    _production_session=False
    _unsubscribe=shared_controller.subscribe(_publish)
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

def _dispose_transport():
    global _process,_server,_unsubscribe,_terminal_deadline,_force_deadline,_kill_deadline,_production_session
    if bpy.app.timers.is_registered(_pulse): bpy.app.timers.unregister(_pulse)
    if _unsubscribe: _unsubscribe(); _unsubscribe=None
    if _server: _server.close(join=False)
    _process=None; _server=None; _terminal_deadline=None; _force_deadline=None
    _kill_deadline=None
    _production_session=False

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
