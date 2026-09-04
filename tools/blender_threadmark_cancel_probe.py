# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise a real invoked Cycles render cancellation in Blender 5.x."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import traceback

import bpy


values = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
if len(values) != 1:
    raise SystemExit("expected -- REPORT_JSON")
REPORT = Path(values[0])
started = time.monotonic()
events = []
report = {"blender_version": bpy.app.version_string, "background": bpy.app.background}
finished = False


def _event(name):
    events.append({"event": name, "elapsed_seconds": time.monotonic() - started})


def _pre(*_args):
    _event("render_pre")


def _post(*_args):
    _event("render_post")


def _complete(*_args):
    _event("render_complete")
    bpy.app.timers.register(_finish, first_interval=0.1)


def _cancelled(*_args):
    _event("render_cancel")
    bpy.app.timers.register(_finish, first_interval=0.1)


HANDLERS = {
    "render_pre": _pre,
    "render_post": _post,
    "render_complete": _complete,
    "render_cancel": _cancelled,
}


def _finish():
    global finished
    if finished:
        return None
    finished = True
    for name, callback in HANDLERS.items():
        handlers = getattr(bpy.app.handlers, name)
        while callback in handlers:
            handlers.remove(callback)
    report["events"] = events
    report["handler_counts_after_remove"] = {
        name: sum(item is callback for item in getattr(bpy.app.handlers, name))
        for name, callback in HANDLERS.items()
    }
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


def _cancel():
    attempts = []
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "IMAGE_EDITOR":
                    continue
                region = next((item for item in area.regions if item.type == "WINDOW"), None)
                if region is None:
                    continue
                with bpy.context.temp_override(window=window, area=area, region=region):
                    attempts.append(
                        {
                            "poll": bpy.ops.render.view_cancel.poll(),
                            "result": sorted(bpy.ops.render.view_cancel("EXEC_DEFAULT")),
                        }
                    )
        report["cancel_attempts"] = attempts
    except Exception:
        report["cancel_error"] = traceback.format_exc()
    return None


def _start():
    try:
        report["invoke_result"] = sorted(bpy.ops.render.render("INVOKE_DEFAULT"))
        bpy.app.timers.register(_watchdog, first_interval=60.0)
    except Exception:
        report["invoke_error"] = traceback.format_exc()
        _finish()
    return None


def _watchdog():
    if not finished:
        report["watchdog"] = True
        _finish()
    return None


def _configure():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 65536
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.object.camera_add(location=(0, 0, 5))
    scene.camera = bpy.context.object
    bpy.ops.mesh.primitive_monkey_add()
    bpy.ops.wm.save_as_mainfile(filepath=str(REPORT.with_suffix(".blend")))
    for name, callback in HANDLERS.items():
        getattr(bpy.app.handlers, name).append(callback)
    report["ready_for_external_f12"] = True
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    bpy.app.timers.register(_watchdog, first_interval=120.0)


_configure()
