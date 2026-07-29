# SPDX-License-Identifier: GPL-3.0-or-later
"""Two-process Blender recovery proof against the real managed solver."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("cancel", "resume"), required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--solver", type=Path, required=True)
    return parser.parse_args(values)


def _load_addon(args):
    sys.path.insert(0, str(args.repo))
    os.environ["CLOTH_NEXT_PPF_EXECUTABLE"] = str(args.solver)
    from cloth_next.blender import registration, solver_test
    registration.register()
    return solver_test


def _make_scene(args):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    cloths = []
    for name, x, pin_mode in (
            ("Recovery Static Cloth", -0.65, "STATIC"),
            ("Recovery Follow Cloth", 0.65, "FOLLOW_ANIMATION")):
        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=9, y_subdivisions=9, size=1.1,
            location=(x, 0.0, 1.35))
        cloth = bpy.context.object
        cloth.name = name
        cloth.cloth_next.enabled = True
        cloth.cloth_next.role = "CLOTH"
        cloth.cloth_next.bake_start = 1
        cloth.cloth_next.bake_end = 12
        cloth.cloth_next.cache_directory = str(args.cache)
        pins = cloth.vertex_groups.new(name="Pins")
        pins.add([0], 1.0, "REPLACE")
        cloth.cloth_next.pinning_enabled = True
        cloth.cloth_next.pin_group = pins.name
        cloth.cloth_next.pin_mode = pin_mode
        if pin_mode == "FOLLOW_ANIMATION":
            cloth.keyframe_insert("location", frame=1)
            cloth.location.z += 0.12
            cloth.location.y += 0.08
            cloth.keyframe_insert("location", frame=12)
        cloths.append(cloth)

    bpy.ops.mesh.primitive_cube_add(
        location=(0.0, 0.0, 0.72), scale=(1.15, 0.58, 0.18))
    collider = bpy.context.object
    collider.name = "Recovery Deforming Collider"
    collider.cloth_next.enabled = True
    collider.cloth_next.role = "COLLIDER"
    collider.cloth_next.collider_motion = "ANIMATED"
    collider.cloth_next.collider_capture_mode = "DEFORMING"
    collider.cloth_next.collider_samples_per_frame = 8
    collider.shape_key_add(name="Basis")
    deform = collider.shape_key_add(name="Breathing")
    for point in deform.data:
        if point.co.z > 0.0:
            point.co.z += 0.35 * (1.0 - min(1.0, abs(point.co.x)))
    deform.value = 0.0
    deform.keyframe_insert("value", frame=1)
    deform.value = 1.0
    deform.keyframe_insert("value", frame=6)
    deform.value = 0.25
    deform.keyframe_insert("value", frame=12)
    collider.location.x = -0.12
    collider.keyframe_insert("location", frame=1)
    collider.location.x = 0.12
    collider.keyframe_insert("location", frame=12)

    scene = bpy.context.scene
    scene.render.fps = 24
    recovery = scene.cloth_next_recovery
    recovery.enabled = True
    recovery.auto_save = True
    recovery.checkpoint_interval = 2
    recovery.keep_saved_states = 3
    recovery.save_on_cancel = True
    recovery.save_on_finish = False
    bpy.context.view_layer.objects.active = cloths[0]
    cloths[0].select_set(True)
    return tuple(cloths), collider


def _drain_until_done(solver_test, worker, *, cancel):
    messages = []
    cancelled = False
    deadline = time.monotonic() + 600
    while worker.is_alive() or not solver_test._queue.empty():
        if time.monotonic() > deadline:
            solver_test.request_cancel()
            raise TimeoutError("recovery integration timed out")
        try:
            message = solver_test._queue.get(timeout=0.1)
        except queue.Empty:
            continue
        messages.append(message)
        if cancel and message[0] == "event":
            event = message[1]
            if (event.phase in {"SIMULATING", "FETCHING"}
                    and int(event.frame_current or 0) >= 2):
                solver_test.request_cancel()
                cancelled = True
    worker.join(timeout=10)
    terminal = [item for item in messages
                if item[0] in {"cancelled", "finished", "error"}]
    if not terminal:
        raise RuntimeError("worker produced no terminal message")
    return terminal[-1], cancelled, messages


def _plan(solver_test, context):
    snapshot = solver_test.validate_scene(context)
    return solver_test.build_run_plan(context, snapshot=snapshot)


def cancel_phase(args, solver_test):
    cloths, collider = _make_scene(args)
    args.cache.mkdir(parents=True, exist_ok=True)
    # Recovery identity deliberately requires a saved .blend path.  Save
    # before planning so the real production path can enable checkpointing;
    # saving only after cancellation creates a valid preview but no
    # cross-restart recovery record.
    bpy.ops.wm.save_as_mainfile(
        filepath=str(args.blend), check_existing=False)
    plan = _plan(solver_test, bpy.context)
    if plan.recovery_options is None:
        raise AssertionError(
            "saved multi-object Bake did not enable recovery: "
            f"scene_key={plan.scene_cache_key!r}; "
            f"param_key={plan.param_cache_key!r}; "
            f"events={plan.export_cache_events!r}")
    solver_test._cancel_event.clear()
    while not solver_test._queue.empty():
        solver_test._queue.get_nowait()
    worker = threading.Thread(
        target=solver_test._worker_main, args=(plan,),
        name="recovery-real-cancel")
    worker.start()
    terminal, requested, messages = _drain_until_done(
        solver_test, worker, cancel=True)
    if terminal[0] != "cancelled" or not requested:
        raise AssertionError(f"controlled cancel failed: {terminal[0]}")
    preview = terminal[3] if len(terminal) > 3 else None
    if preview is None:
        raise AssertionError("cancel did not publish a playable prefix")
    partial_frame_count = solver_test._attach_cancelled_preview(plan, preview)
    if partial_frame_count <= 1:
        raise AssertionError("cancelled prefix has no completed solver frame")
    bpy.ops.wm.save_as_mainfile(
        filepath=str(args.blend), check_existing=False)
    from cloth_next import recovery
    options = plan.recovery_options
    assert options is not None
    record = recovery.load_project(options.metadata_path)
    if record is None or record.state is not recovery.ProjectState.RESUMABLE:
        raise AssertionError("cancel did not publish RESUMABLE metadata")
    if len(record.partial_caches) != 2:
        raise AssertionError("multi-object partial integrity was not persisted")
    for cloth in cloths:
        modifiers = [
            modifier for modifier in cloth.modifiers
            if modifier.type == "MESH_CACHE"]
        if len(modifiers) != 1:
            raise AssertionError(
                f"{cloth.name} has no attached partial playback cache")
    report = {
        "phase": "cancel", "terminal": terminal[0],
        "project": record.project_id, "state": record.state.value,
        "saved_states": [item.frame for item in record.checkpoints],
        "partial_pc2": dict(record.partial_pc2),
        "partial_frames": partial_frame_count,
        "partial_integrity": [
            asdict(item) for item in record.partial_caches],
        "collider": collider.name,
        "collider_samples_per_frame":
            collider.cloth_next.collider_samples_per_frame,
        "pin_modes": {
            cloth.name: cloth.cloth_next.pin_mode for cloth in cloths},
        "blend": str(args.blend), "event_count": len(messages),
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def resume_phase(args, solver_test):
    cloth = bpy.data.objects["Recovery Static Cloth"]
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    settings = bpy.context.scene.cloth_next_recovery
    plan = _plan(solver_test, bpy.context)
    from cloth_next import recovery
    metadata = (
        Path(settings.recovery_directory) / recovery.METADATA_NAME)
    record = recovery.load_project(metadata)
    if record is None:
        raise AssertionError(
            f"saved recovery metadata is missing at {metadata}")
    if plan.scene.param_hash != record.identity.param_key:
        raise AssertionError(
            "restart changed the parameter identity: "
            f"current={plan.scene.param_hash}; "
            f"saved={record.identity.param_key}")
    settings.resume_requested = True
    plan = solver_test._configure_recovery(
        bpy.context, solver_test.validate_scene(bpy.context), plan)
    if plan.recovery_options is None or not plan.recovery_options.resume:
        raise AssertionError(
            f"saved Bake was not selected for resume: "
            f"{settings.status_detail}; current_param={plan.param_cache_key}; "
            f"saved_param={record.identity.param_key if record else 'missing'}")
    completed_before = tuple(
        plan.recovery_options.completed_solver_frames)
    resume_from = plan.recovery_options.resume_from_frame
    solver_test._cancel_event.clear()
    while not solver_test._queue.empty():
        solver_test._queue.get_nowait()
    worker = threading.Thread(
        target=solver_test._worker_main, args=(plan,),
        name="recovery-real-resume")
    worker.start()
    terminal, _requested, messages = _drain_until_done(
        solver_test, worker, cancel=False)
    if terminal[0] != "finished":
        raise AssertionError(f"resume failed: {terminal}")
    targets = solver_test._plan_deformables(plan)
    from cloth_next.bake import pc2
    headers = {
        target.uuid: asdict(pc2.read_header(target.pc2_path))
        for target in targets}
    diagnostics = terminal[2]
    fetched = tuple(diagnostics.fetched_frames)
    logical_solver_frames = tuple(range(1, plan.scene.solver_frame_count + 1))
    if tuple(sorted((*completed_before, *fetched))) != logical_solver_frames:
        raise AssertionError(
            f"resume boundary is not continuous: cached={completed_before}, "
            f"fetched={fetched}, expected={logical_solver_frames}")
    if set(completed_before).intersection(fetched):
        raise AssertionError("resume downloaded an already cached frame")
    solver_test._attach_playback(plan, terminal[1])
    penetration_counts = _boundary_penetrations(
        solver_test, plan, completed_before)
    if any(penetration_counts.values()):
        raise AssertionError(
            f"collider penetration at resume boundary: {penetration_counts}")
    report = {
        "phase": "resume", "terminal": terminal[0],
        "project": plan.scene.project_name,
        "upload_seconds": diagnostics.timings.get("upload", 0.0),
        "resume_from": resume_from,
        "cached_solver_frames": completed_before,
        "fetched_frames": fetched,
        "continuous_solver_frames": logical_solver_frames,
        "boundary_penetrations": penetration_counts,
        "headers": headers, "event_count": len(messages),
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def _boundary_penetrations(solver_test, plan, completed_before):
    """Conservatively reject points strictly inside the collider world AABB."""
    if not completed_before:
        raise AssertionError("resume did not authenticate a partial prefix")
    from mathutils import Vector
    from cloth_next.bake import pc2

    boundary = plan.frame_start + completed_before[-1]
    frames = tuple(
        frame for frame in (boundary, boundary + 1)
        if plan.frame_start <= frame <= plan.frame_end)
    targets = solver_test._plan_deformables(plan)
    cached = {
        target.uuid: tuple(pc2.iter_frames(target.pc2_path))
        for target in targets}
    collider = bpy.data.objects["Recovery Deforming Collider"]
    result = {}
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = collider.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            world_points = [
                evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
            minimum = Vector((
                min(point.x for point in world_points),
                min(point.y for point in world_points),
                min(point.z for point in world_points)))
            maximum = Vector((
                max(point.x for point in world_points),
                max(point.y for point in world_points),
                max(point.z for point in world_points)))
        finally:
            evaluated.to_mesh_clear()
        count = 0
        index = frame - plan.frame_start
        for target in targets:
            obj = bpy.data.objects[target.object_name]
            for position in cached[target.uuid][index]:
                point = obj.matrix_world @ Vector(position)
                if all(
                        minimum[axis] + 1e-5 < point[axis]
                        < maximum[axis] - 1e-5
                        for axis in range(3)):
                    count += 1
        result[str(frame)] = count
    return result


def main():
    args = _args()
    solver_test = _load_addon(args)
    if args.phase == "cancel":
        cancel_phase(args, solver_test)
    else:
        # Register the repository PropertyGroups before opening the saved file
        # so Blender can restore the new Recovery fields from ID properties.
        bpy.ops.wm.open_mainfile(filepath=str(args.blend))
        resume_phase(args, solver_test)
    bpy.ops.wm.quit_blender()


if __name__ == "__main__":
    main()
