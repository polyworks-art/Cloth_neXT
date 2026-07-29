# SPDX-License-Identifier: GPL-3.0-or-later
"""Controlled source-tree Bake retest for an existing Blender scene.

Runs in a separate background Blender process, redirects playback caches to a
caller-owned output directory, bypasses only the optional Companion UI, and
executes the production validation/export/session/cache worker synchronously.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

import bpy


def _arguments() -> argparse.Namespace:
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ()
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(values)


def main() -> None:
    args = _arguments()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["CLOTH_NEXT_PPF_EXECUTABLE"] = str(args.solver.resolve())
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()))

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    addon = importlib.import_module("cloth_next")
    addon.register()
    try:
        from cloth_next.blender import solver_test
        from cloth_next.ppf.schema import envelope

        enabled = tuple(
            obj for obj in bpy.context.scene.objects
            if bool(getattr(getattr(obj, "cloth_next", None), "enabled", False)))
        for obj in enabled:
            settings = obj.cloth_next
            if str(getattr(settings, "role", "")) == "CLOTH":
                settings.cache_directory = str(args.output)

        bpy.context.scene.frame_set(1)
        snapshot = solver_test.validate_scene(bpy.context)
        plan = solver_test.build_run_plan(bpy.context, snapshot=snapshot)
        params = envelope.loads_envelope(
            plan.scene.param_payload, envelope.KIND_PARAM,
            schema_version=int(plan.resolved.schema_version))

        while not solver_test._queue.empty():
            solver_test._queue.get_nowait()
        solver_test._run_started_at = time.monotonic()
        solver_test._worker_main_multi(plan)
        messages = []
        while not solver_test._queue.empty():
            messages.append(solver_test._queue.get_nowait())
        terminal = messages[-1]
        if terminal[0] != "finished":
            raise RuntimeError(json.dumps({
                "terminal": terminal[0],
                "summary": terminal[1] if len(terminal) > 1 else "",
                "details": terminal[2] if len(terminal) > 2 else "",
            }, ensure_ascii=False))

        headers, diagnostics = terminal[1], terminal[2]
        pin_config = params.get("pin_config", {})
        report = {
            "blend": str(args.blend),
            "output": str(args.output),
            "deformables": [target.object_name for target in plan.deformables],
            "frame_count": plan.frame_count,
            "protocol": diagnostics.protocol_version,
            "schema": diagnostics.schema_version,
            "control_server_alive_after": diagnostics.control_server_alive,
            "control_server_exit_code": diagnostics.control_server_exit_code,
            "owned_process_ids_after": diagnostics.owned_process_ids,
            "cleanup_issues": diagnostics.cleanup_issues,
            "fetched_frames": diagnostics.fetched_frames,
            "pin_objects": sorted(pin_config),
            "cache_files": {
                uuid: {
                    "path": str(next(
                        target.pc2_path for target in plan.deformables
                        if target.uuid == uuid)),
                    "frames": header.frame_count,
                    "vertices": header.vertex_count,
                } for uuid, header in headers.items()
            },
        }
        report_path = args.output / "scene-lifecycle-report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print("CLOTH_NEXT_SCENE_RETEST=" + json.dumps(report, sort_keys=True))
    finally:
        addon.unregister()


if __name__ == "__main__":
    main()
