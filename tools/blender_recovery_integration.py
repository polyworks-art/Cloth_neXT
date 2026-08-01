# SPDX-License-Identifier: GPL-3.0-or-later
"""Two-process Blender recovery proof against the real managed solver."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=(
            "cancel", "crash", "fresh", "hard_abort", "resume"),
        required=True)
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
    cloth.cloth_next.bake_end = 24
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


def _drain_until_done(solver_test, worker, *, cancel,
                      terminate_server=False):
    messages = []
    cancelled = False
    terminated = False
    process_id = None
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
        if message[0] == "event":
            event = message[1]
            if event.phase == "RUNTIME_METADATA" and event.process_id:
                process_id = int(event.process_id)
            if (cancel and event.phase in {"SIMULATING", "FETCHING"}
                    and int(event.frame_current or 0) >= 2):
                solver_test.request_cancel()
                cancelled = True
            if (terminate_server and not terminated
                    and process_id is not None
                    and event.phase in {"SIMULATING", "FETCHING"}
                    and int(event.frame_current or 0) >= 2):
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/F"],
                    check=True, capture_output=True, text=True)
                terminated = True
    worker.join(timeout=10)
    terminal = [item for item in messages
                if item[0] in {"cancelled", "finished", "error"}]
    if not terminal:
        raise RuntimeError("worker produced no terminal message")
    return terminal[-1], cancelled, terminated, process_id, messages


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
    terminal, requested, _terminated, _pid, messages = _drain_until_done(
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


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hard_abort_phase(args, solver_test):
    """Hard-exit Blender after the real solver publishes a periodic state."""
    _make_scene(args)
    args.cache.mkdir(parents=True, exist_ok=True)
    plan = _plan(solver_test, bpy.context)
    bpy.ops.wm.save_as_mainfile(
        filepath=str(args.blend), check_existing=False)
    solver_test._cancel_event.clear()
    while not solver_test._queue.empty():
        solver_test._queue.get_nowait()
    worker = threading.Thread(
        target=solver_test._worker_main, args=(plan,),
        name="recovery-real-hard-abort")
    worker.start()
    statuses = []
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            message = solver_test._queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if message[0] != "event":
            if message[0] in {"error", "cancelled", "finished"}:
                raise AssertionError(
                    f"Bake ended before a periodic checkpoint: {message}")
            continue
        event = message[1]
        statuses.append({
            "phase": event.phase,
            "message": event.message,
            "frame": event.frame_current,
        })
        if event.phase != "RECOVERY_SAVED":
            continue
        from cloth_next import recovery
        options = plan.recovery_options
        assert options is not None
        record = recovery.load_project(options.metadata_path)
        if record is None or not record.checkpoints:
            raise AssertionError("checkpoint event had no verified metadata")
        checkpoint = Path(record.checkpoints[-1].checkpoint_path)
        with gzip.open(checkpoint, "rb") as stream:
            decoded_size = len(stream.read())
        param_toml = (Path(record.project_root) / "session" / "param.toml")
        param_text = param_toml.read_text(encoding="utf-8")
        report = {
            "result": "recovery_unverified_resume_pending",
            "solver": {
                "executable": str(args.solver.resolve()),
                "release": "2026-07-26-22-53",
                "protocol": record.identity.protocol_version,
                "schema": record.identity.solver_schema_version,
                "installation_id": "official-2026-07-26-22-53-win64",
            },
            "project_id": record.project_id,
            "server_data_root": record.server_data_root,
            "metadata_path": str(options.metadata_path),
            "data_hash": plan.scene.data_hash,
            "parameter_hash": plan.scene.param_hash,
            "outgoing_recovery_parameters": {
                "scene.auto-save": {"value": 2, "type": "integer"},
                "scene.keep-states": {"value": 3, "type": "integer"},
                "scene.save-state-on-finish": {
                    "value": False, "type": "boolean"},
            },
            "decoded_param_toml": param_text,
            "observed_statuses": statuses,
            "checkpoint": {
                "frame": record.checkpoints[-1].frame,
                "path": str(checkpoint),
                "size": checkpoint.stat().st_size,
                "decoded_size": decoded_size,
                "sha256": _sha256(checkpoint),
                "metadata_sha256": record.checkpoints[-1].checkpoint_sha256,
            },
            "hard_abort_exit_code": 91,
        }
        args.report.write_text(
            json.dumps(report, indent=2), encoding="utf-8")
        with args.report.open("r+b") as stream:
            os.fsync(stream.fileno())
        os._exit(91)
    raise TimeoutError("no periodic solver checkpoint appeared")


def crash_phase(args, solver_test):
    _make_scene(args)
    args.cache.mkdir(parents=True, exist_ok=True)
    plan = _plan(solver_test, bpy.context)
    bpy.ops.wm.save_as_mainfile(
        filepath=str(args.blend), check_existing=False)
    solver_test._cancel_event.clear()
    while not solver_test._queue.empty():
        solver_test._queue.get_nowait()
    worker = threading.Thread(
        target=solver_test._worker_main, args=(plan,),
        name="recovery-real-controlled-crash")
    worker.start()
    terminal, _cancelled, terminated, process_id, messages = (
        _drain_until_done(
            solver_test, worker, cancel=False, terminate_server=True))
    if terminal[0] != "error" or not terminated:
        raise AssertionError(
            f"controlled crash did not reach the error path: {terminal[0]}")
    from cloth_next import recovery
    from cloth_next.bake import pc2
    options = plan.recovery_options
    assert options is not None
    record = recovery.load_project(
        options.metadata_path, verify_checkpoints=False)
    if record is None:
        raise AssertionError("controlled crash lost recovery metadata")
    targets = {
        target.uuid: target for target in solver_test._plan_deformables(plan)}
    partials = {}
    for uuid, value in record.partial_pc2:
        target = targets[uuid]
        path = Path(value)
        expected = pc2.Pc2Header(
            len(target.initial_local), 0.0, 1.0, plan.frame_count)
        partials[uuid] = {
            "path": str(path),
            "exists": path.is_file(),
            "validated_frames": (
                pc2.partial_frame_count(path, expected)
                if path.is_file() else 0),
        }
    checkpoints = [
        {"frame": item.frame, "path": item.checkpoint_path,
         "exists": Path(item.checkpoint_path).is_file(),
         "integrity": item.integrity}
        for item in record.checkpoints]
    report = {
        "phase": "crash",
        "terminal": terminal[0],
        "summary": terminal[1],
        "details": terminal[2],
        "control_server_pid": process_id,
        "state": record.state.value,
        "checkpoints": checkpoints,
        "partial_pc2": partials,
        "event_count": len(messages),
    }
    if not any(item["validated_frames"] > 1
               for item in partials.values()):
        raise AssertionError(
            "controlled crash did not preserve a completed frame prefix")
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
    terminal, _requested, _terminated, _pid, messages = _drain_until_done(
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
    if args.report.is_file():
        existing = json.loads(args.report.read_text(encoding="utf-8"))
        if existing.get("hard_abort_exit_code") == 91:
            output = (Path(existing["server_data_root"])
                      / existing["project_id"] / "session" / "output")
            stdout = output.parent / "stdout.log"
            command = (
                stdout.read_text(encoding="utf-8", errors="replace")
                .splitlines()[0]
                if stdout.is_file()
                else (f"{args.solver.resolve()} --path "
                      f"{output.parent} --output {output} --load=-1"))
            report = {
                **existing,
                "result": "passed",
                "resume": {
                    **report,
                    "command": command,
                    "first_frame_after_checkpoint": (
                        int(existing["checkpoint"]["frame"]) + 1),
                },
            }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def fresh_phase(args, solver_test):
    result = bpy.ops.clothnext.recovery_start_fresh("EXEC_DEFAULT")
    if result != {"FINISHED"}:
        raise AssertionError(f"Start Fresh failed: {result}")
    cloth = bpy.data.objects["Recovery Cloth"]
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    plan = _plan(solver_test, bpy.context)
    if plan.recovery_options is None or plan.recovery_options.resume:
        raise AssertionError("fresh Bake reused recovery state")
    solver_test._cancel_event.clear()
    while not solver_test._queue.empty():
        solver_test._queue.get_nowait()
    worker = threading.Thread(
        target=solver_test._worker_main, args=(plan,),
        name="recovery-real-fresh")
    worker.start()
    terminal, _requested, _terminated, _pid, messages = _drain_until_done(
        solver_test, worker, cancel=False)
    if terminal[0] != "finished":
        raise AssertionError(f"fresh Bake failed: {terminal}")
    diagnostics = terminal[2]
    report = {
        "phase": "fresh", "terminal": terminal[0],
        "project": plan.scene.project_name,
        "launch_id": diagnostics.solver_launch_id,
        "process_id": diagnostics.process_id,
        "protocol": diagnostics.protocol_version,
        "schema": diagnostics.schema_version,
        "fetched_frames": diagnostics.fetched_frames,
        "cleanup_issues": diagnostics.cleanup_issues,
        "event_count": len(messages),
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def main():
    args = _args()
    solver_test = _load_addon(args)
    if args.phase == "cancel":
        cancel_phase(args, solver_test)
    elif args.phase == "hard_abort":
        hard_abort_phase(args, solver_test)
    elif args.phase == "crash":
        crash_phase(args, solver_test)
    else:
        # Register the repository PropertyGroups before opening the saved file
        # so Blender can restore the new Recovery fields from ID properties.
        bpy.ops.wm.open_mainfile(filepath=str(args.blend))
        if args.phase == "fresh":
            fresh_phase(args, solver_test)
        else:
            resume_phase(args, solver_test)
    bpy.ops.wm.quit_blender()


if __name__ == "__main__":
    main()
