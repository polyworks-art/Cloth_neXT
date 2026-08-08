# SPDX-License-Identifier: GPL-3.0-or-later
"""Real production-operator Newton Bake and reopen acceptance gate."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--mode", choices=("create", "reopen"), required=True)
    return parser.parse_args(values)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _digest_positions(obj):
    value = [[float(component) for component in vertex.co]
             for vertex in obj.data.vertices]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def _playback_modifier(obj):
    from cloth_next.blender.playback_cache import \
        has_cloth_next_playback_marker
    return next((item for item in obj.modifiers
                 if has_cloth_next_playback_marker(obj, item)), None)


def _reopen(args):
    sys.path.insert(0, str(args.repo))
    os.environ["CLOTHNEXT_NEWTON_PYTHON"] = str(args.python)
    from cloth_next.bake import cache_metadata, pc2
    from cloth_next.blender import registration
    registration.register()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    try:
        cloth = bpy.data.objects["Newton Bake Gate Cloth"]
        modifier = _playback_modifier(cloth)
        if modifier is None:
            raise AssertionError("Newton PC2 playback modifier is missing after reopen")
        path = Path(bpy.path.abspath(modifier.filepath)).resolve()
        header = pc2.read_header(path)
        inspection = cache_metadata.inspect_cache(path)
        report.update({
            "reopen_result": "passed", "reopen_cache_path": str(path),
            "reopen_header": asdict(header),
            "reopen_cache_condition": inspection.condition.value,
            "reopen_modifier_present": True,
            "reopen_source_digest": _digest_positions(cloth),
        })
        if not inspection.usable or header.frame_count != 6:
            raise AssertionError("Newton cache is not usable after reopening Blender")
        _write(args.report, report)
    except Exception as exc:
        report.update({"reopen_result": "failed", "error": str(exc),
                       "traceback": traceback.format_exc()})
        _write(args.report, report)
    bpy.ops.wm.quit_blender()


def _create(args):
    sys.path.insert(0, str(args.repo))
    os.environ["CLOTHNEXT_NEWTON_PYTHON"] = str(args.python)
    from cloth_next.bake.controller import shared_controller
    from cloth_next.bake.status import BakeState
    from cloth_next.bake import cache_metadata, pc2
    from cloth_next.blender import registration
    registration.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=7, y_subdivisions=7,
                                    size=2.0, location=(0.0, 0.0, 1.5))
    cloth = bpy.context.object
    cloth.name = "Newton Bake Gate Cloth"
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.persistent_export_id = "newton-bake-gate-cloth"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 6
    cache_dir = args.report.parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cloth.cloth_next.cache_directory = str(cache_dir)
    group = cloth.vertex_groups.new(name="Newton Bake Pins")
    top = [vertex.index for vertex in cloth.data.vertices
           if vertex.co.y > 0.99]
    group.add((top[0], top[-1]), 1.0, "REPLACE")
    cloth.cloth_next.pinning_enabled = True
    cloth.cloth_next.pin_group = group.name
    scene = bpy.context.scene
    scene.frame_start = 1; scene.frame_end = 6
    scene.render.fps = 24; scene.render.fps_base = 1.0
    scene.cloth_next_solver.backend = "NEWTON"
    scene.cloth_next_solver.quality_preset = "LOW"
    source_digest = _digest_positions(cloth)
    operator_result = sorted(bpy.ops.clothnext.bake("EXEC_DEFAULT"))
    started = time.monotonic()

    def wait():
        snapshot = shared_controller.snapshot()
        if time.monotonic() - started > 300.0:
            _write(args.report, {"result": "failed", "error": "timeout",
                                 "operator_result": operator_result})
            bpy.ops.wm.quit_blender(); return None
        if snapshot.state not in {BakeState.FINISHED, BakeState.ERROR,
                                  BakeState.CANCELLED}:
            return 0.05
        try:
            if snapshot.state is not BakeState.FINISHED:
                raise RuntimeError(snapshot.error_details or snapshot.status_message)
            modifier = _playback_modifier(cloth)
            if modifier is None:
                raise AssertionError("production Bake did not attach PC2 playback")
            path = Path(bpy.path.abspath(modifier.filepath)).resolve()
            header = pc2.read_header(path)
            inspection = cache_metadata.inspect_cache(path)
            metadata = inspection.metadata or {}
            report = {
                "gate": "newton_offline_bake", "result": "passed",
                "operator_result": operator_result,
                "controller_state": snapshot.state.value,
                "solver_mode": snapshot.solver_mode,
                "cache_path": str(path), "header": asdict(header),
                "cache_condition": inspection.condition.value,
                "backend_identity": metadata.get("details", {}).get("backend"),
                "source_digest_before": source_digest,
                "source_digest_after": _digest_positions(cloth),
                "elapsed_seconds": time.monotonic() - started,
            }
            if (operator_result != ["FINISHED"] or not inspection.usable
                    or header.frame_count != 6
                    or report["backend_identity"] != "NEWTON"
                    or report["source_digest_after"] != source_digest):
                raise AssertionError(f"Newton Bake invariant failed: {report}")
            bpy.ops.wm.save_as_mainfile(filepath=str(args.blend))
            _write(args.report, report)
        except Exception as exc:
            _write(args.report, {"gate": "newton_offline_bake",
                                 "result": "failed", "error": str(exc),
                                 "traceback": traceback.format_exc(),
                                 "operator_result": operator_result})
        bpy.ops.wm.quit_blender()
        return None

    bpy.app.timers.register(wait, first_interval=0.05)


def main():
    args = _args()
    if args.mode == "reopen":
        _reopen(args)
    else:
        _create(args)


if __name__ == "__main__":
    main()
