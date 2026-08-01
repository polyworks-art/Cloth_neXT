# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender artist-path Recovery gate.

This intentionally uses only the registered load handler and public operators.
It never sets ``resume_requested``, builds a RunPlan, or calls a worker entry
point directly. Run both phases in UI-capable Blender processes so registered
timers, the production companion gate, and modal Bake startup execute normally.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

import bpy


EXIT_HARD_ABORT = 91


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("hard-abort", "resume"), required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--solver", type=Path, required=True)
    return parser.parse_args(values)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_report(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    with temporary.open("r+b") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _owned_child_commands(parent_pid: int | None) -> tuple[str, ...]:
    """Read only direct children of the Cloth NeXt-owned server process."""
    if not parent_pid or os.name != "nt":
        return ()
    command = (
        "$p=Get-CimInstance Win32_Process -Filter \"ParentProcessId="
        f"{int(parent_pid)}\"; $p | ForEach-Object {{$_.CommandLine}}")
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=5.0, check=False,
            shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return ()
    return tuple(line.strip() for line in completed.stdout.splitlines()
                 if line.strip())


def _register(args):
    sys.path.insert(0, str(args.repo))
    os.environ["CLOTH_NEXT_PPF_EXECUTABLE"] = str(args.solver)
    os.environ["CLOTH_NEXT_DEVELOPER_COMPANION"] = "1"
    # Blender's ``sys.executable`` is blender.exe, not a Python interpreter.
    # The developer companion must run in the separately installed CPython.
    os.environ.setdefault(
        "CLOTH_NEXT_COMPANION_PYTHON", shutil.which("python") or "python")
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
    cloth.name = "Recovery UI Cloth"
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 4
    cloth.cloth_next.cache_directory = str(args.cache)
    scene = bpy.context.scene
    scene.name = "Recovery UI Gate"
    scene.render.fps = 24
    settings = scene.cloth_next_recovery
    settings.enabled = True
    settings.auto_save = True
    settings.checkpoint_interval = 2
    settings.keep_saved_states = 3
    settings.save_on_cancel = True
    settings.save_on_finish = False
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    return cloth


def _hard_abort(args, solver_test):
    _make_scene(args)
    args.cache.mkdir(parents=True, exist_ok=True)
    operator_result = set(bpy.ops.clothnext.bake("EXEC_DEFAULT"))
    if not operator_result.intersection({"FINISHED", "RUNNING_MODAL"}):
        raise AssertionError(f"production Bake did not start: {operator_result}")
    # Production preparation has now selected and stored the exact Recovery
    # directory. Save that display cache so load_post can scope ownership
    # without guessing among other projects in the same cache directory.
    bpy.ops.wm.save_as_mainfile(filepath=str(args.blend), check_existing=False)
    deadline = time.monotonic() + 600.0

    def wait_for_checkpoint():
        if time.monotonic() > deadline:
            raise TimeoutError("production Bake produced no periodic checkpoint")
        settings = bpy.context.scene.cloth_next_recovery
        root = str(settings.recovery_directory or "").strip()
        if not root:
            return 0.05
        from cloth_next import recovery
        metadata = Path(root) / recovery.METADATA_NAME
        verdict = recovery.evaluate_resumable(metadata)
        if not verdict.available:
            return 0.05
        record = recovery.load_project(metadata)
        if record is None or not record.checkpoints:
            return 0.05
        checkpoint = Path(record.checkpoints[-1].checkpoint_path)
        with gzip.open(checkpoint, "rb") as stream:
            decoded_size = len(stream.read())
        snapshot = solver_test.shared_controller.snapshot()
        report = {
            "gate": "artist_recovery_ui",
            "phase": "hard_abort",
            "result": "checkpoint_verified_before_hard_abort",
            "operator_result": sorted(operator_result),
            "blend": str(args.blend.resolve()),
            "scene": bpy.context.scene.name,
            "project_id": record.project_id,
            "server_data_root": record.server_data_root,
            "metadata_path": str(metadata),
            "recovery_directory_saved_in_blend": root,
            "checkpoint": {
                "frame": record.checkpoints[-1].frame,
                "path": str(checkpoint),
                "size": checkpoint.stat().st_size,
                "decoded_size": decoded_size,
                "sha256": _sha256(checkpoint),
                "metadata_sha256": record.checkpoints[-1].checkpoint_sha256,
            },
            "controller_state": snapshot.state.value,
            "owned_solver_pid": snapshot.solver_process_id,
            "persistent_handler": hasattr(
                solver_test._on_load_post_refresh_recovery,
                "_bpy_persistent"),
            "load_handler_count": bpy.app.handlers.load_post.count(
                solver_test._on_load_post_refresh_recovery),
            "hard_abort_exit_code": EXIT_HARD_ABORT,
        }
        _write_report(args.report, report)
        os._exit(EXIT_HARD_ABORT)

    bpy.app.timers.register(wait_for_checkpoint, first_interval=0.05)


def _resume(args, solver_test):
    existing = json.loads(args.report.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(args.blend))
    deadline = time.monotonic() + 600.0
    observations = {
        "controller_states": [], "saw_upload": False, "saw_build": False,
        "first_frame_after_checkpoint": None, "solver_commands": (),
    }
    resume_started = False
    selected_project = None
    operator_result = None
    reopen_evidence = None

    def _drive_resume_step():
        nonlocal resume_started, selected_project, operator_result, reopen_evidence
        if time.monotonic() > deadline:
            raise TimeoutError("artist Recovery Resume gate timed out")
        settings = bpy.context.scene.cloth_next_recovery
        snapshot = solver_test.shared_controller.snapshot()
        if (not observations["controller_states"]
                or observations["controller_states"][-1] != snapshot.state.value):
            observations["controller_states"].append(snapshot.state.value)
        observations["saw_upload"] |= snapshot.state.value == "UPLOADING"
        observations["saw_build"] |= snapshot.state.value == "BUILDING"
        if (not observations["solver_commands"]
                and snapshot.state.value in {"SIMULATING", "FETCHING"}):
            commands = _owned_child_commands(snapshot.solver_process_id)
            if commands:
                observations["solver_commands"] = commands
        checkpoint_frame = int(existing["checkpoint"]["frame"])
        if (snapshot.current_frame is not None
                and int(snapshot.current_frame) > checkpoint_frame
                and observations["first_frame_after_checkpoint"] is None):
            observations["first_frame_after_checkpoint"] = int(snapshot.current_frame)

        if not resume_started:
            # This is the only readiness source: the persistent load_post hook
            # plus its delayed timer. No refresh function is called here.
            if settings.status == "Recovery Check Failed":
                raise AssertionError(settings.status_detail)
            if not settings.resumable:
                return 0.05
            poll_result = solver_test.CLOTHNEXT_OT_recovery_resume_latest.poll(
                bpy.context)
            if not poll_result:
                return 0.05
            from cloth_next import recovery
            metadata = Path(settings.recovery_directory) / recovery.METADATA_NAME
            record = recovery.load_project(metadata)
            if record is None:
                raise AssertionError("selected Recovery metadata no longer parses")
            selected_project = record.project_id
            reopen_evidence = {
                "resumable": bool(settings.resumable),
                "latest_checkpoint_frame": int(
                    settings.latest_checkpoint_frame),
                "operator_poll": bool(poll_result),
                "status": settings.status,
                "status_detail": settings.status_detail,
                "recovery_directory": settings.recovery_directory,
                "selected_metadata_path": str(metadata),
                "primary_banner": (
                    f"Recovery checkpoint found \u00b7 Frame "
                    f"{settings.latest_checkpoint_frame}"),
                "persistent_handler": hasattr(
                    solver_test._on_load_post_refresh_recovery,
                    "_bpy_persistent"),
                "load_handler_count": bpy.app.handlers.load_post.count(
                    solver_test._on_load_post_refresh_recovery),
            }
            operator_result = set(
                bpy.ops.clothnext.recovery_resume_latest("EXEC_DEFAULT"))
            if "CANCELLED" in operator_result:
                raise AssertionError(
                    f"Resume operator cancelled: {settings.status_detail}")
            resume_started = True
            return 0.05

        if snapshot.state.value in {"ERROR", "CANCELLED"}:
            raise AssertionError(
                f"Resume ended in {snapshot.state.value}: "
                f"{snapshot.error_summary or snapshot.status_message}")
        if snapshot.state.value != "FINISHED":
            return 0.05

        from cloth_next.bake import pc2
        pc2_files = tuple(sorted(args.cache.glob("*.pc2")))
        if not pc2_files:
            raise AssertionError("production Resume published no PC2 cache")
        headers = {path.name: {
            "vertex_count": pc2.read_header(path).vertex_count,
            "frame_count": pc2.read_header(path).frame_count,
            "size": path.stat().st_size,
        } for path in pc2_files}
        command = next((value for value in observations["solver_commands"]
                        if "--load" in value),
                       observations["solver_commands"][0]
                       if observations["solver_commands"] else "unavailable")
        resumed_frame = observations["first_frame_after_checkpoint"]
        if selected_project != existing["project_id"]:
            raise AssertionError("Resume selected a different solver project")
        if observations["saw_upload"] or observations["saw_build"]:
            raise AssertionError(
                "Resume rebuilt or uploaded a fresh solver project")
        if "--load=-1" not in command:
            raise AssertionError(
                f"solver did not receive the resume command: {command}")
        if resumed_frame is None or not (
                checkpoint_frame < resumed_frame <=
                int(bpy.context.scene.frame_end)):
            raise AssertionError(
                f"first resumed frame is invalid: {resumed_frame}")
        report = {
            **existing,
            "result": "passed",
            "reopen": reopen_evidence,
            "resume": {
                "operator_result": sorted(operator_result),
                "selected_project_id": selected_project,
                "same_project": selected_project == existing["project_id"],
                "controller_state_after_click": observations["controller_states"],
                "scene_upload_observed": observations["saw_upload"],
                "project_build_observed": observations["saw_build"],
                "first_frame_after_checkpoint": observations[
                    "first_frame_after_checkpoint"],
                "solver_command": command,
                "pc2": headers,
            },
        }
        _write_report(args.report, report)
        bpy.ops.wm.quit_blender()
        return None

    def drive_resume():
        try:
            return _drive_resume_step()
        except Exception as exc:  # timer failures must become evidence
            failed_snapshot = solver_test.shared_controller.snapshot()
            _write_report(args.report, {
                **existing,
                "result": "failed",
                "gate_error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                    "controller_states": observations["controller_states"],
                    "controller_error_summary": failed_snapshot.error_summary,
                    "controller_error_details": failed_snapshot.error_details,
                    "controller_error_code": failed_snapshot.error_code,
                },
            })
            bpy.ops.wm.quit_blender()
            return None

    bpy.app.timers.register(drive_resume, first_interval=0.1)


def main():
    args = _arguments()
    solver_test = _register(args)
    if args.phase == "hard-abort":
        _hard_abort(args, solver_test)
    else:
        _resume(args, solver_test)


if __name__ == "__main__":
    main()
