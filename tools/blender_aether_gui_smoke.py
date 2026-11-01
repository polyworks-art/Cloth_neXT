# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Visible-UI smoke test of the AETHER Engine workflow (needs a window, not -b).

    "Start Blender (AETHER Test).cmd" --python tools/blender_aether_gui_smoke.py -- --output <dir>

Runs inside the regular Blender UI of the private test environment: the
Engine is chosen in Preferences, the Physics > Cloth NeXt panels are shown and
captured, and every action goes through the same operators the panel buttons
call (Live Start/Pause/Reset/Stop, Bake with the Bake window, Cancel, Resume
Bake). Screenshots are taken of Blender areas and of the "Cloth NeXt Bake"
window only, never of the desktop. Results: <output>/gui_smoke.json.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import bpy
import numpy as np

ARGS = argparse.ArgumentParser()
ARGS.add_argument("--output", type=Path, required=True)
ARGS.add_argument("--module", default="bl_ext.user_default.cloth_next")
ARGS = ARGS.parse_args(sys.argv[sys.argv.index("--") + 1:])
OUT = ARGS.output.resolve()
OUT.mkdir(parents=True, exist_ok=True)
M = ARGS.module
REPORT: dict = {"blender": bpy.app.version_string, "checks": {}, "notes": []}


def mod(name):
    import importlib
    return importlib.import_module(f"{M}.{name}")


def check(name, ok, **detail):
    REPORT["checks"][name] = {"ok": bool(ok), **detail}
    print("GUI-SMOKE", name, "PASS" if ok else "FAIL", detail, flush=True)
    (OUT / "gui_smoke.json").write_text(json.dumps(REPORT, indent=1, default=str))


def engine_processes():
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if line.startswith('"AETHER Engine.exe"'))


# ---------------------------------------------------------------- UI helpers
def areas(kind):
    window = bpy.context.window_manager.windows[0]
    for area in window.screen.areas:
        if area.type == kind:
            return window, area, next(r for r in area.regions if r.type == "WINDOW")
    return None


def ensure_properties_physics():
    found = areas("PROPERTIES")
    if found is None:
        return
    _w, area, _r = found
    area.spaces.active.context = "PHYSICS"
    area.tag_redraw()


def shot(kind, name):
    found = areas(kind)
    if found is None:
        return
    window, area, region = found
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=str(OUT / name))


def simulation_panel_shot(name):
    """The Physics tab maximised and scrolled to the Simulation (Bake) panel."""
    found = areas("PROPERTIES")
    if found is None:
        return
    window, area, region = found
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screen_full_area()
    STEPS.insert(0, lambda: _scroll_panel(name))


def _scroll_panel(name):
    window, area, region = areas("PROPERTIES")
    with bpy.context.temp_override(window=window, area=area, region=region):
        for _ in range(60):
            bpy.ops.view2d.scroll_down(deltay=60)
    area.tag_redraw()
    STEPS.insert(0, lambda: _finish_panel_shot(name))


def _finish_panel_shot(name):
    window, area, region = areas("PROPERTIES")
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=str(OUT / name))
        bpy.ops.screen.back_to_previous()


def _bake_window_rect():
    """Screen rectangle of the visible 'Cloth NeXt Bake' window, or None."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []
    titles = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if "Cloth NeXt" in buf.value:
                titles.append(buf.value)
                if buf.value.startswith("Cloth NeXt Bake"):
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    found.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumWindows(visit, 0)
    REPORT["cloth_next_windows_seen"] = sorted(set(titles) | set(REPORT.get("cloth_next_windows_seen", [])))
    return found[0] if found else None


def bake_window_shot(name):
    """Capture only the 'Cloth NeXt Bake' window (never the desktop)."""
    rect = _bake_window_rect()
    if rect is None:
        return False
    left, top, right, bottom = rect
    target = str(OUT / name).replace("'", "''")
    script = ("Add-Type -AssemblyName System.Drawing;"
              f"$i=New-Object System.Drawing.Bitmap {right - left},{bottom - top};"
              "$g=[System.Drawing.Graphics]::FromImage($i);"
              f"$g.CopyFromScreen({left},{top},0,0,$i.Size);$i.Save('{target}')")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=30)
    return result.returncode == 0


def select(obj):
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def call(op, **kw):
    found = areas("PROPERTIES")
    window, area, region = found
    with bpy.context.temp_override(window=window, area=area, region=region):
        return op("EXEC_DEFAULT", **kw)


# ---------------------------------------------------------------- scene
def build_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.render.fps = 24
    scene.frame_set(1)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=40, y_subdivisions=40, size=1.2, location=(0, 0, 1.0))
    sheet = bpy.context.object
    sheet.name = "SmokeSheet"
    bpy.ops.clothnext.add_physics()
    s = sheet.cloth_next
    s.bake_start, s.bake_end = 1, 72
    s.cache_directory = str(OUT / "cache")
    co = [(v.co.x, v.co.y) for v in sheet.data.vertices]
    corners = sorted({min(range(len(co)), key=lambda i: sx * co[i][0] + sy * co[i][1]) for sx in (-1, 1)
                      for sy in (1,)})
    group = sheet.vertex_groups.new(name="Pins")
    group.add(corners, 1.0, "REPLACE")
    s.pinning_enabled, s.pin_group, s.pin_mode, s.pin_constraint_type = True, "Pins", "STATIC", "HARD"
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.25, location=(-0.5, 0.0, 0.55))
    body = bpy.context.object
    body.name = "SmokeBody"
    bpy.ops.clothnext.add_physics()
    bpy.ops.clothnext.set_object_type(role="COLLIDER")
    body.cloth_next.collider_motion = "ANIMATED"
    body.keyframe_insert("location", frame=1)
    body.location = (0.5, 0.0, 0.55)
    body.keyframe_insert("location", frame=72)
    select(sheet)
    ensure_properties_physics()
    return sheet


# ---------------------------------------------------------------- steps
STATE: dict = {}
STEPS: list = []


def wait(seconds):
    def step():
        if "until" not in STATE:
            STATE["until"] = time.monotonic() + seconds
        if time.monotonic() < STATE["until"]:
            STEPS.insert(0, step)
            return
        STATE.pop("until")
    return step


def s_preferences():
    prefs = bpy.context.preferences.addons[M].preferences
    REPORT["engine_before"] = prefs.solver_engine
    prefs.solver_engine = "AETHER"           # the Preferences Engine selector
    backends = mod("blender.solver_backends")
    status = mod("blender.aether_engine_backend").engine_status(refresh=True)
    check("preferences_aether_selection", prefs.solver_engine == "AETHER"
          and backends.active_backend_id(bpy.context.scene).value == "AETHER_ENGINE", engine_before=REPORT["engine_before"])
    check("readiness_detection", status.ready, title=status.title, detail=status.detail)


def s_scene():
    STATE["sheet"] = build_scene()
    STATE["rest"] = np.array([v.co[:] for v in STATE["sheet"].data.vertices])
    ui = mod("blender.physics_ui")
    model = ui._bake_panel_model(bpy.context, ui._active_backend_status(bpy.context))
    check("bake_button_enabled", model.enabled, reason=model.reason)


def s_panel_shot():
    simulation_panel_shot("01_panel_aether_ready.png")


def s_live_start():
    STATE["objects_before"] = {o.name for o in bpy.data.objects}
    t0 = time.monotonic()
    result = call(bpy.ops.clothnext.aether_live_start)
    live = mod("blender.aether_engine_live")
    STATE["live_startup"] = time.monotonic() - t0
    check("live_start", result == {"FINISHED"} and live.is_running() and STATE["sheet"].hide_get(),
          startup_s=round(STATE["live_startup"], 2))


def s_live_running():
    live = mod("blender.aether_engine_live")
    s = live._session
    preview = bpy.data.objects.get(s.previews[0][1])
    moved = float(np.abs(np.array([v.co[:] for v in preview.data.vertices]) - STATE["rest"]).max())
    check("live_preview_moves", preview is not None and moved > 0.02 and s.frames_shown > 10,
          frames_shown=s.frames_shown, moved=round(moved, 3), status=live.status_line())
    shot("VIEW_3D", "02_live_viewport.png")
    simulation_panel_shot("03_live_panel.png")


def s_live_pause():
    live = mod("blender.aether_engine_live")
    call(bpy.ops.clothnext.aether_live_toggle_pause)
    STATE["paused_frame"] = None


def s_live_paused_check():
    live = mod("blender.aether_engine_live")
    s = live._session
    if STATE["paused_frame"] is None:
        STATE["paused_frame"] = s.last_frame_seen
        STEPS.insert(0, wait(0.8))
        STEPS.insert(1, s_live_paused_check)
        return
    check("live_pause", s.paused and s.last_frame_seen == STATE["paused_frame"], frame=s.last_frame_seen)


def s_live_reset():
    call(bpy.ops.clothnext.aether_live_reset)


def s_live_reset_check():
    s = mod("blender.aether_engine_live")._session
    check("live_reset", s.last_frame_seen == 0, frame=s.last_frame_seen)


def s_live_stop():
    call(bpy.ops.clothnext.aether_live_stop)


def s_live_stop_check():
    sheet = STATE["sheet"]
    same = np.array_equal(np.array([v.co[:] for v in sheet.data.vertices]), STATE["rest"])
    objects = {o.name for o in bpy.data.objects}
    check("live_stop_restores", same and objects == STATE["objects_before"] and not sheet.hide_get()
          and not mod("blender.aether_engine_live").is_running(), engine_processes=engine_processes())


def s_bake(quality, name):
    def step():
        bpy.context.scene.cloth_next_solver.aether_bake_quality = quality
        STATE["bake_started"] = time.monotonic()
        STATE["bake_name"] = name
        STATE["window_shot"] = False
        result = call(bpy.ops.clothnext.bake)
        check(f"{name}_started", result in ({"FINISHED"}, {"RUNNING_MODAL"}), quality=quality)
    return step


def s_wait_bake(name, cancel_at=None, shot_progress=True):
    def step():
        controller = mod("bake.controller").shared_controller
        snap = controller.snapshot()
        backend = mod("blender.aether_engine_backend")
        job = backend._job
        if job is not None and job.runner is not None and shot_progress and not STATE["window_shot"] \
                and job.runner.progress.frames_done > 10:
            STATE["window_shot"] = bake_window_shot(f"{name}_bake_window.png")
            REPORT.setdefault("progress_samples", []).append(
                {"bake": name, "state": snap.state.value, "label": snap.activity_label,
                 "progress": [snap.progress_current, snap.progress_total]})
        if cancel_at is not None and job is not None and job.runner is not None \
                and job.runner.progress.frames_done >= cancel_at and not STATE.get("cancelled"):
            STATE["cancelled"] = True
            mod("blender.solver_test").request_cancel()   # what the Bake window's Cancel calls
        if snap.active or backend.is_running():
            STEPS.insert(0, step)
            return
        STATE[f"{name}_result"] = {"state": snap.state.value, "message": snap.status_message,
                                   "error": snap.error_summary, "code": snap.error_code,
                                   "seconds": round(time.monotonic() - STATE["bake_started"], 2)}
    return step


def playback_check(name, frames):
    def step():
        sheet = STATE["sheet"]
        st = mod("blender.solver_test")
        pc2 = mod("bake.pc2")
        result = STATE[f"{name}_result"]
        modifier = next((m for m in sheet.modifiers if st.is_cloth_next_playback_modifier(sheet, m)
                         and m.filepath), None)
        ok = result["state"] == "FINISHED" and modifier is not None
        error, count = None, 0
        if ok:
            path = Path(bpy.path.abspath(modifier.filepath))
            data = list(pc2.iter_frames(path))
            count = len(data)
            error = 0.0
            for k in range(0, count, 7):
                bpy.context.scene.frame_set(1 + k)
                ev = sheet.evaluated_get(bpy.context.evaluated_depsgraph_get())
                mesh = ev.to_mesh()
                co = np.empty(len(mesh.vertices) * 3, np.float32)
                mesh.vertices.foreach_get("co", co)
                ev.to_mesh_clear()
                error = max(error, float(np.abs(co.reshape(-1, 3) - np.asarray(data[k])).max()))
            STATE[f"{name}_pc2"] = path.read_bytes()
            bpy.context.scene.frame_set(36)
        check(f"{name}_playback", ok and count == frames and error is not None and error < 1e-5,
              result=result, pc2_frames=count, playback_error=error, window_captured=STATE["window_shot"])
        shot("VIEW_3D", f"{name}_playback_frame36.png")
    return step


def s_cancel_check():
    result = STATE["cancel_result"]
    backend = mod("blender.aether_engine_backend")
    backend._recovery_cache.clear()
    offered = backend.recovery_available(bpy.context)
    simulation_panel_shot("08_panel_resume_offered.png")
    check("cancel", result["state"] == "CANCELLED" and offered, result=result, resume_offered=offered,
          engine_processes=engine_processes())


def s_resume():
    STATE["bake_started"] = time.monotonic()
    STATE["window_shot"] = False
    result = call(bpy.ops.clothnext.aether_resume_bake)
    check("resume_started", result == {"FINISHED"})


def s_resume_check():
    result = STATE["resume_result"]
    same = STATE.get("resume_pc2") == STATE.get("fitting_pc2")
    check("resume_bit_identical_to_uninterrupted_fitting", result["state"] == "FINISHED" and same,
          result=result, bit_identical=same)


def s_resume_pc2():
    sheet = STATE["sheet"]
    st = mod("blender.solver_test")
    modifier = next(m for m in sheet.modifiers if st.is_cloth_next_playback_modifier(sheet, m) and m.filepath)
    STATE["resume_pc2"] = Path(bpy.path.abspath(modifier.filepath)).read_bytes()


def s_error_message():
    sheet = STATE["sheet"]
    sheet.cloth_next.pressure.shrink_percent = 5.0
    ui = mod("blender.physics_ui")
    model = ui._bake_panel_model(bpy.context, ui._active_backend_status(bpy.context))
    simulation_panel_shot("09_panel_unsupported_feature.png")
    check("unsupported_feature_message", not model.enabled and "AETHER" in model.reason, reason=model.reason)
    STEPS.insert(2, _reset_shrink)


def _reset_shrink():
    STATE["sheet"].cloth_next.pressure.shrink_percent = 0.0


def s_orphans():
    check("no_orphaned_engine_processes", engine_processes() == 0, engine_processes=engine_processes())


def s_finish():
    REPORT["result"] = "PASS" if all(c["ok"] for c in REPORT["checks"].values()) else "FAIL"
    (OUT / "gui_smoke.json").write_text(json.dumps(REPORT, indent=1, default=str))
    print("GUI-SMOKE RESULT", REPORT["result"], flush=True)
    bpy.ops.wm.quit_blender()


STEPS += [s_preferences, s_scene, wait(1.0), s_panel_shot,
          s_live_start, wait(2.5), s_live_running, s_live_pause, s_live_paused_check,
          s_live_reset, wait(0.6), s_live_reset_check, s_live_stop, wait(1.0), s_live_stop_check,
          s_bake("NORMAL", "normal"), s_wait_bake("normal"), playback_check("normal", 72),
          s_bake("FITTING", "fitting"), s_wait_bake("fitting"), playback_check("fitting", 72),
          s_bake("FITTING", "cancel"), s_wait_bake("cancel", cancel_at=30, shot_progress=False), wait(1.0),
          s_cancel_check, s_resume, s_wait_bake("resume"), s_resume_pc2, s_resume_check,
          s_error_message, wait(2.0), s_orphans, s_finish]


def tick():
    if not STEPS:
        return None
    step = STEPS.pop(0)
    try:
        step()
    except Exception as exc:  # noqa: BLE001 - recorded; the run continues
        import traceback
        check(getattr(step, "__name__", "step"), False, exception=traceback.format_exc()[-1500:])
    return 0.1


bpy.app.timers.register(tick, first_interval=3.0)
