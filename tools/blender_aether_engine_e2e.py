# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender acceptance of the AETHER Engine backend (Milestone 5).

    python tools/run_aether_blender_e2e.py            # isolated Blender, this checkout
    blender --background --factory-startup --python tools/blender_aether_engine_e2e.py -- \
        --module bl_ext.user_default.cloth_next --output <dir> [--only name ...]

The engine is chosen through the Preferences Engine property, exactly as an
artist would. Scenarios (each a fresh scene):

 static_collider    sheet falling onto a static mesh collider; PC2 playback matches
 animated_collider  keyframed character-style mesh collider moving through the drape
 multi_material     two Cloth objects with different materials; both cached
 animated_pins      pins following shape-key animation (Follow Animation)
 sewing             two separate panels sewn by a Sewing definition
 live               Live Simulation: start, frames delivered, pause, reset, stop, state restored
 cancel             cancel during a bake: no playback change, resumable partial
 kill_resume        AETHER Engine.exe killed mid-bake, Resume Bake: bit-identical PC2
 device_lost        injected device fault, explicit error, Resume Bake: bit-identical PC2
 incompatible       resume after a preset change is refused with CNX-E129
 lifecycle          repeated startup/shutdown leaves no AETHER Engine.exe running
 offset_range       Bake Start 11: frame mapping and Mesh Cache playback
 gaia_routing       GAIA selection still routes to the unchanged solver path

Results and measurements (main-thread tick time, startup, throughput, frame
delivery, GPU memory) go to <output>/report.json.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import addon_utils
import bpy
import numpy as np

M = None            # the add-on package
REPORT: dict = {"blender": bpy.app.version_string, "scenarios": {}, "measurements": {}}


# ---------------------------------------------------------------- helpers
def engine_processes() -> int:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return sum(1 for line in out.splitlines() if line.startswith('"AETHER Engine.exe"'))


def gpu_used_mb() -> float | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return float(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


def clear_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    scene = bpy.context.scene
    scene.cloth_next_sewing_definitions.clear()
    scene.render.fps, scene.render.fps_base = 24, 1.0
    scene.frame_set(1)


def add_grid(name, size, location, subdivisions=20, rotation=(0.0, 0.0, 0.0)):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=subdivisions, y_subdivisions=subdivisions, size=size,
                                    location=location, rotation=rotation)
    obj = bpy.context.object
    obj.name = name
    return obj


def make_cloth(obj, cache: Path, start: int, end: int, preset: str | None = None):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.clothnext.add_physics()
    s = obj.cloth_next
    s.bake_start, s.bake_end = start, end
    s.cache_directory = str(cache)
    if preset:
        s.material.preset = preset
    return obj


def make_collider(obj, animated: bool = False):
    bpy.context.view_layer.objects.active = obj
    bpy.ops.clothnext.add_physics()
    bpy.ops.clothnext.set_object_type(role="COLLIDER")
    obj.cloth_next.collider_motion = "ANIMATED" if animated else "STATIC"
    return obj


def pin(obj, indices, mode="STATIC", constraint="HARD"):
    group = obj.vertex_groups.new(name="Pins")
    group.add(list(indices), 1.0, "REPLACE")
    s = obj.cloth_next
    s.pinning_enabled = True
    s.pin_group = group.name
    s.pin_mode = mode
    s.pin_constraint_type = constraint


def corners(obj):
    co = [(v.co.x, v.co.y) for v in obj.data.vertices]
    return sorted({min(range(len(co)), key=lambda i: sx * co[i][0] + sy * co[i][1])
                   for sx in (-1, 1) for sy in (-1, 1)})


def playback_error(obj) -> tuple[float, int, Path]:
    """Max |evaluated - PC2| over all frames, PC2 frame count, PC2 path."""
    st = importlib.import_module(f"{M}.blender.solver_test")
    pc2 = importlib.import_module(f"{M}.bake.pc2")
    modifier = next(m for m in obj.modifiers if st.is_cloth_next_playback_modifier(obj, m) and m.filepath)
    path = Path(bpy.path.abspath(modifier.filepath))
    frames = list(pc2.iter_frames(path))
    start = int(round(modifier.frame_start))
    error = 0.0
    for k, expected in enumerate(frames):
        bpy.context.scene.frame_set(start + k)
        ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = ev.to_mesh()
        try:
            co = np.empty(len(mesh.vertices) * 3, np.float32)
            mesh.vertices.foreach_get("co", co)
            error = max(error, float(np.abs(co.reshape(-1, 3) - np.asarray(expected)).max()))
        finally:
            ev.to_mesh_clear()
    return error, len(frames), path


def bake(**kw):
    backend = importlib.import_module(f"{M}.blender.aether_engine_backend")
    ticks = []
    t0 = time.monotonic()
    resume = kw.pop("resume", False)
    cancel_after = kw.pop("cancel_after_frames", None)
    kill_after = kw.pop("kill_after_frames", None)
    gpu = {"peak": gpu_used_mb(), "idle": gpu_used_mb()}
    job = backend.AetherBakeJob(bpy.context, resume=resume, open_window=False, **kw)
    backend._job = job
    killed = False
    try:
        while not job.done:
            if cancel_after is not None and job.runner is not None and \
                    job.runner.progress.frames_done >= cancel_after:
                job.cancel()
            if kill_after is not None and not killed and job.runner is not None and \
                    job.runner.progress.frames_done >= kill_after and job.runner.engine is not None:
                job.runner.engine.proc.kill()
                killed = True
            s = time.perf_counter()
            interval = job.tick()
            ticks.append((time.perf_counter() - s) * 1000.0)
            used = gpu_used_mb() if len(ticks) % 20 == 0 else None
            if used is not None and (gpu["peak"] is None or used > gpu["peak"]):
                gpu["peak"] = used
            if not job.done:
                time.sleep(min(0.05, interval or 0.0))
    finally:
        backend._job = None
    snap = importlib.import_module(f"{M}.bake.controller").shared_controller.snapshot()
    result = {"state": snap.state.value, "error": snap.error_summary, "code": snap.error_code,
              "message": job.message, "seconds": round(time.monotonic() - t0, 2),
              "tick_ms_max": round(max(ticks), 1) if ticks else None,
              "tick_ms_p95": round(sorted(ticks)[int(0.95 * (len(ticks) - 1))], 1) if ticks else None,
              "gpu_mb_idle": gpu["idle"], "gpu_mb_peak": gpu["peak"]}
    if job.runner is not None:
        p = job.runner.progress
        result.update(frames=p.frames_done, resumed_from=p.resumed_from, verified=p.verified,
                      engine_ms=round(p.engine_ms, 1), timings=job.runner.timings)
    importlib.import_module(f"{M}.bake.controller").shared_controller.reset()
    return result


def scenario(name):
    def wrap(fn):
        SCENARIOS[name] = fn
        return fn
    return wrap


SCENARIOS: dict = {}


# ---------------------------------------------------------------- scenarios
@scenario("static_collider")
def s_static(out: Path):
    clear_scene()
    sheet = make_cloth(add_grid("Sheet", 1.0, (0, 0, 1.0), 24), out, 1, 30)
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.35, location=(0, 0, 0.45))
    make_collider(bpy.context.object)
    r = bake()
    err, frames, path = playback_error(sheet)
    first = np.asarray(next(importlib.import_module(f"{M}.bake.pc2").iter_frames(path)))
    last = np.asarray(list(importlib.import_module(f"{M}.bake.pc2").iter_frames(path))[-1])
    r.update(playback_error=err, pc2_frames=frames, fell=float(first[:, 2].max() - last[:, 2].max()),
             backend=sheet.cloth_next.baked_solver_backend, cache_label=_cache_label())
    r["ok"] = (r["state"] == "FINISHED" and frames == 30 and err < 1e-5 and r["fell"] > 0.05
               and r["backend"] == "AETHER_ENGINE" and "stale" not in r["cache_label"].lower())
    return r


def _cache_label():
    ui = importlib.import_module(f"{M}.blender.physics_ui")
    return ui._cache_state(bpy.context)[1]


@scenario("animated_collider")
def s_animated(out: Path):
    clear_scene()
    sheet = make_cloth(add_grid("Drape", 1.2, (0, 0, 0.9), 24), out, 1, 36)
    pin(sheet, corners(sheet)[:2])
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=0.22, location=(-0.6, 0.0, 0.75))
    body = make_collider(bpy.context.object, animated=True)
    body.keyframe_insert("location", frame=1)
    body.location = (0.6, 0.0, 0.75)
    body.keyframe_insert("location", frame=36)
    r = bake()
    err, frames, _ = playback_error(sheet)
    r.update(playback_error=err, pc2_frames=frames)
    r["ok"] = r["state"] == "FINISHED" and frames == 36 and err < 1e-5
    return r


@scenario("multi_material")
def s_multi(out: Path):
    clear_scene()
    a = make_cloth(add_grid("Silk", 0.8, (-0.6, 0, 1.0), 16), out, 1, 24, preset="SILK")
    b = make_cloth(add_grid("Denim", 0.8, (0.6, 0, 1.0), 16), out, 1, 24, preset="DENIM")
    pin(a, corners(a)[2:])
    pin(b, corners(b)[2:])
    r = bake()
    ea, fa, pa = playback_error(a)
    eb, fb, pb = playback_error(b)
    meta = importlib.import_module(f"{M}.bake.cache_metadata")
    ma = json.loads(meta.sidecar_path(pa).read_text())["details"]["aether"]["material"]
    mb = json.loads(meta.sidecar_path(pb).read_text())["details"]["aether"]["material"]
    r.update(playback_error=max(ea, eb), frames=[fa, fb], silk=ma, denim=mb)
    r["ok"] = (r["state"] == "FINISHED" and fa == fb == 24 and max(ea, eb) < 1e-5
               and ma["youngs"] != mb["youngs"] and ma["bend"] != mb["bend"])
    return r


@scenario("animated_pins")
def s_pins(out: Path):
    clear_scene()
    sheet = add_grid("Flag", 1.0, (0, 0, 1.0), 20, rotation=(1.5708, 0, 0))
    top = [i for i, v in enumerate(sheet.data.vertices) if v.co.y > 0.49]
    sheet.shape_key_add(name="Basis")
    lift = sheet.shape_key_add(name="Lift")
    for i in top:
        lift.data[i].co.x += 0.4
    lift.value = 0.0
    lift.keyframe_insert("value", frame=1)
    lift.value = 1.0
    lift.keyframe_insert("value", frame=30)
    make_cloth(sheet, out, 1, 30)
    pin(sheet, top, mode="FOLLOW_ANIMATION")
    r = bake()
    err, frames, path = playback_error(sheet)
    data = list(importlib.import_module(f"{M}.bake.pc2").iter_frames(path))
    moved = float(np.abs(np.asarray(data[-1])[top, 0] - np.asarray(data[0])[top, 0]).max())
    r.update(playback_error=err, pin_travel_x=moved)
    r["ok"] = r["state"] == "FINISHED" and frames == 30 and moved > 0.3
    return r


@scenario("sewing")
def s_sewing(out: Path):
    clear_scene()
    a = add_grid("PanelA", 0.4, (-0.24, 0, 0.8), 12, rotation=(1.5708, 0, 0))
    b = add_grid("PanelB", 0.4, (0.24, 0, 0.8), 12, rotation=(1.5708, 0, 0))
    make_cloth(a, out, 1, 48)
    make_cloth(b, out, 1, 48)
    pin(a, [i for i, v in enumerate(a.data.vertices) if v.co.y > 0.19 and v.co.x < -0.19])
    pin(b, [i for i, v in enumerate(b.data.vertices) if v.co.y > 0.19 and v.co.x > 0.19])
    side_a = sorted((i for i, v in enumerate(a.data.vertices) if v.co.x > 0.199), key=lambda i: a.data.vertices[i].co.y)
    side_b = sorted((i for i, v in enumerate(b.data.vertices) if v.co.x < -0.199), key=lambda i: b.data.vertices[i].co.y)
    sewing = importlib.import_module(f"{M}.blender.sewing")
    sewing._commit(bpy.context.scene, a, b, side_a, side_b)
    r = bake()
    pc2 = importlib.import_module(f"{M}.bake.pc2")
    _, _, pa = playback_error(a)
    _, _, pb = playback_error(b)
    la, lb = list(pc2.iter_frames(pa))[-1], list(pc2.iter_frames(pb))[-1]
    wa = np.asarray(la) @ np.array(a.matrix_world)[:3, :3].T + np.array(a.matrix_world)[:3, 3]
    wb = np.asarray(lb) @ np.array(b.matrix_world)[:3, :3].T + np.array(b.matrix_world)[:3, 3]
    gap = float(np.linalg.norm(wa[side_a] - wb[side_b], axis=1).max())
    r.update(seam_gap_m=gap, start_gap_m=0.08)
    r["ok"] = r["state"] == "FINISHED" and gap < 0.02
    return r


@scenario("material_zones")
def s_zones(out: Path):
    """A hanging sheet whose lower half is a Material Zone with 1/20 the Stretch Resistance."""
    clear_scene()
    sheet = add_grid("Zoned", 0.6, (0, 0, 1.0), 16, rotation=(1.5708, 0, 0))
    make_cloth(sheet, out, 1, 36)
    world = [sheet.matrix_world @ v.co for v in sheet.data.vertices]
    pin(sheet, [i for i, p in enumerate(world) if p.z > 1.29])
    base = sheet.cloth_next.material.stretch_resistance if hasattr(sheet.cloth_next.material, "stretch_resistance") \
        else 1000.0
    for o in bpy.context.view_layer.objects:
        o.select_set(o == sheet)
    bpy.context.view_layer.objects.active = sheet
    assert bpy.ops.clothnext.add_material_zone() == {"FINISHED"}
    zone = sheet.cloth_next.material_zones[-1]
    zone.name = "Soft lower half"
    override = zone.overrides.add()
    override.key, override.value = "young-mod", float(base) / 20.0
    zones = importlib.import_module(f"{M}.blender.material_zones")
    lower = [zone.token if sum(world[i].z for i in f.vertices) / len(f.vertices) < 1.0 else 0
             for f in sheet.data.polygons]
    zones.write_owners(sheet, lower)
    r = bake()
    err, frames, path = playback_error(sheet)
    data = list(importlib.import_module(f"{M}.bake.pc2").iter_frames(path))
    rest, last = np.asarray(data[0], np.float64), np.asarray(data[-1], np.float64)
    edges = np.array([tuple(e.vertices) for e in sheet.data.edges])
    strain = np.linalg.norm(last[edges[:, 0]] - last[edges[:, 1]], axis=1) / \
        np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1) - 1.0
    z = np.array([0.5 * (world[a].z + world[b].z) for a, b in edges])
    meta = importlib.import_module(f"{M}.bake.cache_metadata")
    recorded = json.loads(meta.sidecar_path(path).read_text())["details"]["aether"]["material_zones"]
    r.update(playback_error=err, pc2_frames=frames, zone_max_strain=round(float(strain[z < 1.0].max()), 4),
             base_max_strain=round(float(strain[z > 1.0].max()), 4), sidecar_zones=recorded)
    r["ok"] = (r["state"] == "FINISHED" and frames == 36 and err < 1e-5
               and r["zone_max_strain"] > 3.0 * r["base_max_strain"] and len(recorded) == 1
               and recorded[0]["triangles"] > 0)
    return r


@scenario("attachments")
def s_attachments(out: Path):
    """Cloth -> animated Collider and Cloth -> Cloth Object Attachments."""
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0.0, 0.0, 1.0))
    box = make_collider(bpy.context.object, animated=True)
    box.keyframe_insert("location", frame=1)
    box.location = (0.5, 0.0, 1.0)
    box.keyframe_insert("location", frame=36)
    upper = make_cloth(add_grid("Upper", 0.5, (0.0, -0.2, 0.6), 10, rotation=(1.5708, 0, 0)), out, 1, 36)
    lower = make_cloth(add_grid("Lower", 0.5, (0.0, -0.2, 0.08), 10, rotation=(1.5708, 0, 0)), out, 1, 36)
    world = lambda o: [o.matrix_world @ v.co for v in o.data.vertices]
    uw, lw = world(upper), world(lower)
    top = [i for i, p in enumerate(uw) if p.z > 0.84]
    bottom = [i for i, p in enumerate(uw) if p.z < 0.36]
    lower_top = [i for i, p in enumerate(lw) if p.z > 0.32]
    box_face = [i for i, v in enumerate(box.data.vertices) if v.co.y < -0.1]
    att = importlib.import_module(f"{M}.blender.object_attachments")
    scene = bpy.context.scene
    att._commit_attachment(scene, upper, box, top, box_face, smart=True)
    att._commit_attachment(scene, lower, upper, lower_top, bottom, smart=True)
    rest_gap = {i: min((lw[i] - uw[j]).length for j in bottom) for i in lower_top}
    r = bake()
    pc2 = importlib.import_module(f"{M}.bake.pc2")
    _, _, pu = playback_error(upper)
    _, _, pl = playback_error(lower)
    fu, fl = np.asarray(list(pc2.iter_frames(pu))[-1]), np.asarray(list(pc2.iter_frames(pl))[-1])
    mu, ml = np.array(upper.matrix_world), np.array(lower.matrix_world)
    wu = fu @ mu[:3, :3].T + mu[:3, 3]
    wl = fl @ ml[:3, :3].T + ml[:3, 3]
    follow_x = float(np.mean(wu[top, 0] - np.array([uw[i].x for i in top])))
    gaps = [float(np.min(np.linalg.norm(wu[bottom] - wl[i], axis=1))) for i in lower_top]
    r.update(top_followed_x=round(follow_x, 3), box_moved_x=0.5, lower_gap_max=round(max(gaps), 4),
             lower_gap_rest_max=round(max(rest_gap.values()), 4))
    r["ok"] = (r["state"] == "FINISHED" and abs(follow_x - 0.5) < 0.05 and max(gaps) < max(rest_gap.values()) + 0.03)
    return r


@scenario("live")
def s_live(out: Path):
    clear_scene()
    sheet = make_cloth(add_grid("LiveSheet", 1.0, (0, 0, 1.0), 24), out, 1, 48)
    pin(sheet, corners(sheet)[:2])
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0, 0, 0.4))
    make_collider(bpy.context.object)
    live = importlib.import_module(f"{M}.blender.aether_engine_live")
    before_co = np.array([v.co[:] for v in sheet.data.vertices])
    objects_before = {o.name for o in bpy.data.objects}
    frame_before = bpy.context.scene.frame_current
    t0 = time.monotonic()
    session = live.start(bpy.context)
    startup = time.monotonic() - t0
    preview = bpy.data.objects[session.previews[0][1]]
    session.play()
    shown, ticks, t1 = 0, [], time.monotonic()
    while time.monotonic() - t1 < 3.0:
        s = time.perf_counter()
        assert session.tick()
        ticks.append((time.perf_counter() - s) * 1000.0)
        time.sleep(1 / 60)
    shown = session.frames_shown
    moved = float(np.abs(np.array([v.co[:] for v in preview.data.vertices]) - before_co).max())
    session.pause()
    time.sleep(0.3)
    session.tick()
    f_paused = session.last_frame_seen
    time.sleep(0.5)
    session.tick()
    paused_ok = session.last_frame_seen == f_paused
    session.reset()
    time.sleep(0.2)
    session.tick()
    reset_ok = session.last_frame_seen == 0
    hidden = sheet.hide_get()
    live.stop()
    restored = (np.array_equal(np.array([v.co[:] for v in sheet.data.vertices]), before_co)
                and {o.name for o in bpy.data.objects} == objects_before and not sheet.hide_get()
                and bpy.context.scene.frame_current == frame_before)
    time.sleep(1.0)
    r = {"startup_s": round(startup, 2), "frames_displayed_3s": shown, "display_fps": round(shown / 3.0, 1),
         "tick_ms_p95": round(sorted(ticks)[int(0.95 * (len(ticks) - 1))], 2), "tick_ms_max": round(max(ticks), 2),
         "moved": moved, "paused_ok": paused_ok, "reset_ok": reset_ok, "original_hidden_while_live": hidden,
         "restored": restored, "engine_processes_after": engine_processes()}
    r["ok"] = (shown > 10 and moved > 0.01 and paused_ok and reset_ok and hidden and restored
               and r["engine_processes_after"] == 0)
    return r


def _resume_scene(out: Path, end=48):
    clear_scene()
    sheet = make_cloth(add_grid("ResumeSheet", 1.0, (0, 0, 1.0), 24), out, 1, end)
    pin(sheet, corners(sheet)[:2])
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0, 0, 0.4))
    make_collider(bpy.context.object)
    return sheet


@scenario("cancel")
def s_cancel(out: Path):
    sheet = _resume_scene(out, end=200)
    r = bake(cancel_after_frames=10)
    backend = importlib.import_module(f"{M}.blender.aether_engine_backend")
    backend._recovery_cache.clear()
    st = importlib.import_module(f"{M}.blender.solver_test")
    has_playback = any(st.is_cloth_next_playback_modifier(sheet, m) and m.filepath for m in sheet.modifiers)
    r.update(recovery_offered=backend.recovery_available(bpy.context), playback_attached=has_playback)
    time.sleep(1.0)
    r["engine_processes_after"] = engine_processes()
    r["ok"] = (r["state"] == "CANCELLED" and r["recovery_offered"] and not has_playback
               and r["engine_processes_after"] == 0)
    return r


def _reference(out: Path) -> bytes:
    sheet = _resume_scene(out / "ref")
    r = bake()
    assert r["state"] == "FINISHED", r
    return playback_error(sheet)[2].read_bytes()


@scenario("kill_resume")
def s_kill(out: Path):
    ref = _reference(out)
    sheet = _resume_scene(out / "run")
    first = bake(kill_after_frames=30)
    resumed = bake(resume=True)
    data = playback_error(sheet)[2].read_bytes() if resumed["state"] == "FINISHED" else b""
    r = {"interrupted": first, "resumed": resumed, "bit_identical": data == ref}
    r["ok"] = (first["state"] == "ERROR" and first["code"] == "CNX-E164" and resumed["state"] == "FINISHED"
               and resumed["resumed_from"] is not None and r["bit_identical"])
    return r


@scenario("device_lost")
def s_lost(out: Path):
    ref = _reference(out)
    sheet = _resume_scene(out / "run")
    first = bake(testing_env={"AETHER_ENGINE_TEST_FAULT_FRAME": "29"})
    resumed = bake(resume=True)
    data = playback_error(sheet)[2].read_bytes() if resumed["state"] == "FINISHED" else b""
    r = {"interrupted": first, "resumed": resumed, "bit_identical": data == ref}
    r["ok"] = (first["state"] == "ERROR" and first["code"] == "CNX-E164" and "GPU was lost" in first["error"]
               and resumed["state"] == "FINISHED" and r["bit_identical"])
    return r


@scenario("incompatible")
def s_incompatible(out: Path):
    _resume_scene(out)
    first = bake(kill_after_frames=30)
    bpy.context.scene.cloth_next_solver.aether_bake_quality = "NORMAL"
    resumed = bake(resume=True)
    r = {"interrupted": first["state"], "resume": resumed}
    r["ok"] = (resumed["state"] == "ERROR" and resumed["code"] == "CNX-E129"
               and "presets are never changed silently" in resumed["error"])
    bpy.context.scene.cloth_next_solver.aether_bake_quality = "FITTING"
    return r


@scenario("lifecycle")
def s_lifecycle(out: Path):
    clear_scene()
    make_cloth(add_grid("Cycle", 0.6, (0, 0, 1.0), 10), out, 1, 12)
    live = importlib.import_module(f"{M}.blender.aether_engine_live")
    times = []
    for _ in range(5):
        t0 = time.monotonic()
        s = live.start(bpy.context)
        s.play()
        for _ in range(10):
            s.tick()
            time.sleep(0.02)
        live.stop()
        times.append(round(time.monotonic() - t0, 2))
    for _ in range(3):
        r = bake()
        assert r["state"] == "FINISHED", r
    time.sleep(1.5)
    r = {"cycles_s": times, "engine_processes_after": engine_processes()}
    r["ok"] = r["engine_processes_after"] == 0
    return r


@scenario("offset_range")
def s_offset(out: Path):
    clear_scene()
    sheet = make_cloth(add_grid("Offset", 1.0, (0, 0, 1.0), 16), out, 11, 40)
    pin(sheet, corners(sheet)[:2])
    rest = np.array([v.co[:] for v in sheet.data.vertices], np.float32)
    r = bake()
    err, frames, path = playback_error(sheet)
    pc2 = importlib.import_module(f"{M}.bake.pc2")
    first = np.asarray(next(pc2.iter_frames(path)))
    st = importlib.import_module(f"{M}.blender.solver_test")
    modifier = next(m for m in sheet.modifiers if st.is_cloth_next_playback_modifier(sheet, m) and m.filepath)
    r.update(playback_error=err, pc2_frames=frames, modifier_frame_start=modifier.frame_start,
             rest_at_sample0=bool(np.array_equal(first, rest)))
    r["ok"] = (r["state"] == "FINISHED" and frames == 30 and modifier.frame_start == 11 and err < 1e-5
               and r["rest_at_sample0"])
    return r


@scenario("gaia_routing")
def s_gaia(out: Path):
    backends = importlib.import_module(f"{M}.blender.solver_backends")
    prefs = bpy.context.preferences.addons[M].preferences
    original = prefs.aether_engine_chosen
    routed = backends.active_backend_id(bpy.context.scene).value
    prefs.aether_engine_chosen = False
    engines = importlib.import_module(f"{M}.updater.engines")
    registry, _e = importlib.import_module(f"{M}.blender.preferences")._read_registry()
    gaia_selected = engines.selected_engine(registry) == engines.GAIA
    routed_gaia = backends.active_backend_id(bpy.context.scene).value
    prefs.aether_engine_chosen = original
    r = {"aether_routes_to": routed, "without_aether_choice_routes_to": routed_gaia,
         "gaia_selected_in_registry": gaia_selected}
    r["ok"] = routed == "AETHER_ENGINE" and routed_gaia == ("PPF" if gaia_selected else "AETHER_ENGINE")
    return r


@scenario("performance")
def s_performance(out: Path):
    """10k / 50k vertex drapes: bake throughput, main-thread ticks, GPU memory, live frame rate."""
    rows = []
    live = importlib.import_module(f"{M}.blender.aether_engine_live")
    for subdivisions in (100, 224):
        for quality in ("NORMAL", "FITTING"):
            clear_scene()
            sheet = make_cloth(add_grid("Big", 1.0, (0, 0, 0.8), subdivisions), out / f"{subdivisions}{quality}", 1, 49)
            bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0, 0, 0.35), segments=48, ring_count=24)
            make_collider(bpy.context.object)
            bpy.context.scene.cloth_next_solver.aether_bake_quality = quality
            r = bake()
            rows.append({"vertices": len(sheet.data.vertices), "quality": quality, "mode": "bake",
                         "state": r["state"], "seconds": r["seconds"],
                         "frames_per_s": round(48 / max(r["seconds"], 1e-6), 2),
                         "engine_step_ms_per_frame": round(r.get("engine_ms", 0.0) / 48, 1),
                         "engine_startup_s": round(r.get("timings", {}).get("engine_startup_s", 0.0), 2),
                         "tick_ms_max": r["tick_ms_max"], "tick_ms_p95": r["tick_ms_p95"],
                         "gpu_mb_idle": r["gpu_mb_idle"], "gpu_mb_peak": r["gpu_mb_peak"]})
        bpy.context.scene.cloth_next_solver.aether_bake_quality = "FITTING"
        t0 = time.monotonic()
        session = live.start(bpy.context)
        startup = time.monotonic() - t0
        session.play()
        ticks, t1 = [], time.monotonic()
        while time.monotonic() - t1 < 4.0:
            s0 = time.perf_counter()
            session.tick()
            ticks.append((time.perf_counter() - s0) * 1000.0)
            time.sleep(1 / 60)
        shown = session.frames_shown
        gpu = gpu_used_mb()
        live.stop()
        rows.append({"vertices": len(sheet.data.vertices), "quality": "NORMAL", "mode": "live",
                     "startup_s": round(startup, 2), "display_fps": round(shown / 4.0, 1),
                     "tick_ms_p95": round(sorted(ticks)[int(0.95 * (len(ticks) - 1))], 2),
                     "tick_ms_max": round(max(ticks), 2), "gpu_mb": gpu})
    REPORT["measurements"]["performance"] = rows
    return {"rows": rows, "ok": all(r.get("state", "FINISHED") == "FINISHED" for r in rows)}


def main() -> int:
    global M
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    M = args.module
    args.output.mkdir(parents=True, exist_ok=True)
    addon_utils.enable(M, default_set=True)
    prefs = bpy.context.preferences.addons[M].preferences
    prefs.auto_launch_bake_window = False
    prefs.solver_engine = "AETHER"
    backend = importlib.import_module(f"{M}.blender.aether_engine_backend")
    status = backend.engine_status(refresh=True)
    REPORT["engine"] = {"ready": status.ready, "title": status.title, "detail": status.detail}
    bpy.context.scene.cloth_next_solver.aether_bake_quality = "FITTING"
    ok = status.ready
    for name, fn in SCENARIOS.items():
        if args.only and name not in args.only:
            continue
        folder = args.output / name
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        t0 = time.monotonic()
        try:
            result = fn(folder)
        except Exception as exc:  # noqa: BLE001 - recorded as a failed scenario
            import traceback
            result = {"ok": False, "exception": traceback.format_exc()}
        result["wall_s"] = round(time.monotonic() - t0, 1)
        REPORT["scenarios"][name] = result
        ok = ok and bool(result.get("ok"))
        print(json.dumps({name: result.get("ok"), **({"exception": result["exception"][-800:]}
                                                       if "exception" in result else {})}), flush=True)
        (args.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")
    REPORT["result"] = "PASS" if ok else "FAIL"
    (args.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"result": REPORT["result"]}), flush=True)
    addon_utils.disable(M)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
