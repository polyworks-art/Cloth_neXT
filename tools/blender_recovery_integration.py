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
    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=31, y_subdivisions=31, size=2.0,
        location=(0.0, 0.0, 1.0))
    cloth = bpy.context.object
    cloth.name = "Recovery Cloth"
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 12
    cloth.cloth_next.cache_directory = str(args.cache)
    scene = bpy.context.scene
    scene.render.fps = 24
    recovery = scene.cloth_next_recovery
    recovery.enabled = True
    recovery.auto_save = True
    recovery.checkpoint_interval = 2
    recovery.keep_saved_states = 3
    recovery.save_on_cancel = True
    recovery.save_on_finish = False
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    return cloth


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
    cloth = _make_scene(args)
    args.cache.mkdir(parents=True, exist_ok=True)
    plan = _plan(solver_test, bpy.context)
    bpy.ops.wm.save_as_mainfile(
        filepath=str(args.blend), check_existing=False)
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
    from cloth_next import recovery
    options = plan.recovery_options
    assert options is not None
    record = recovery.load_project(options.metadata_path)
    if record is None or record.state is not recovery.ProjectState.RESUMABLE:
        raise AssertionError("cancel did not publish RESUMABLE metadata")
    report = {
        "phase": "cancel", "terminal": terminal[0],
        "project": record.project_id, "state": record.state.value,
        "saved_states": [item.frame for item in record.checkpoints],
        "partial_pc2": dict(record.partial_pc2),
        "blend": str(args.blend), "event_count": len(messages),
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def resume_phase(args, solver_test):
    cloth = bpy.data.objects["Recovery Cloth"]
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    settings = bpy.context.scene.cloth_next_recovery
    settings.resume_requested = True
    plan = _plan(solver_test, bpy.context)
    if plan.recovery_options is None or not plan.recovery_options.resume:
        from cloth_next import recovery
        metadata = (Path(settings.recovery_directory)
                    / recovery.METADATA_NAME)
        record = recovery.load_project(metadata)
        raise AssertionError(
            f"saved Bake was not selected for resume: "
            f"{settings.status_detail}; current_param={plan.param_cache_key}; "
            f"saved_param={record.identity.param_key if record else 'missing'}")
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
    report = {
        "phase": "resume", "terminal": terminal[0],
        "project": plan.scene.project_name,
        "upload_seconds": diagnostics.timings.get("upload", 0.0),
        "fetched_frames": diagnostics.fetched_frames,
        "headers": headers, "event_count": len(messages),
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


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
