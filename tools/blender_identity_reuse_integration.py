# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-bpy regression proof for Scene export reuse and Recovery PARAM identity.

Run with::

    blender --background --factory-startup --python \
        tools/blender_identity_reuse_integration.py -- --cache <dir> --report <json>
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next import recovery  # noqa: E402
from cloth_next.blender import registration, solver_test  # noqa: E402


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--solver", type=Path)
    return parser.parse_args(values)


def _payload_bytes(value) -> bytes:
    return value.read_bytes() if hasattr(value, "read_bytes") else bytes(value)


def _make_scene(cache: Path):
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=7, y_subdivisions=7, size=2.0,
        location=(0.0, 0.0, 1.0))
    cloth = bpy.context.object
    cloth.name = "Identity Regression Cloth"
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 8
    cloth.cloth_next.cache_directory = str(cache)

    scene = bpy.context.scene
    scene.render.fps = 24
    scene.render.fps_base = 1.0
    scene.frame_start = 1
    scene.frame_end = 8
    scene.frame_set(1)
    settings = scene.cloth_next_recovery
    settings.enabled = True
    settings.auto_save = True
    settings.checkpoint_interval = 2
    settings.keep_saved_states = 3
    settings.save_on_cancel = True
    settings.save_on_finish = True
    bpy.context.view_layer.objects.active = cloth
    cloth.select_set(True)
    return cloth


def _resolved_identity(resolved) -> dict:
    return {
        "installation_id": solver_test._resolved_installation_id(resolved),
        "release_tag": solver_test._resolved_release_tag(resolved),
        "package_version": resolved.package_version,
        "protocol_version": resolved.protocol_version,
        "schema_version": resolved.schema_version,
        "executable": str(resolved.executable_path),
    }


def _publish_checkpoint(options, cache: Path):
    project_root = cache / "integration-server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True, exist_ok=True)
    record = recovery.create_project(
        options.metadata_path, project_id="identity-regression-project",
        identity=options.identity,
        server_data_root=cache / "integration-server",
        project_root=project_root)
    record = recovery.transition(
        options.metadata_path, record, recovery.ProjectState.RUNNING)
    checkpoint = output / "state_2.bin.gz"
    checkpoint.write_bytes(gzip.compress(b"cloth-next-real-bpy-state-2"))
    record = recovery.confirm_saved_states(
        options.metadata_path, record, (2,), keep=3)
    return recovery.transition(
        options.metadata_path, record, recovery.ProjectState.RESUMABLE)


def main():
    args = _arguments()
    args.cache = args.cache.resolve()
    args.report = args.report.resolve()
    if args.solver is not None:
        os.environ["CLOTH_NEXT_PPF_EXECUTABLE"] = str(args.solver.resolve())
    if args.cache.exists() and any(args.cache.iterdir()):
        raise RuntimeError(f"integration cache must be empty: {args.cache}")
    args.cache.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)

    registration.register()
    try:
        cloth = _make_scene(args.cache)
        preferences_before = solver_test._solver_selection_key(bpy.context)
        cold_snapshot = solver_test.validate_scene(bpy.context)
        cold = solver_test.build_run_plan(
            bpy.context, snapshot=cold_snapshot)
        cold_param = _payload_bytes(cold.scene.param_payload)
        resolved = _resolved_identity(cold.resolved)
        preferences_after = solver_test._solver_selection_key(bpy.context)
        options = cold.recovery_options
        assert options is not None

        # A same-process unchanged Bake must hit before evaluated mesh capture.
        warm_snapshot = solver_test.validate_scene(bpy.context)
        warm = solver_test.build_run_plan(
            bpy.context, snapshot=warm_snapshot)
        assert cold.scene_cache_key == warm.scene_cache_key
        assert warm.export_timings.get("scene_early_cache_hit") == 1.0
        assert warm.export_timings.get("to_mesh_count", 0.0) == 0.0
        assert warm.scene.data_hash == cold.scene.data_hash

        saved = _publish_checkpoint(options, args.cache)
        cached_param = solver_test._payload_cache_for(cloth).lookup(
            "param", cold.param_cache_key)
        assert cached_param.hit
        cached_param.path.unlink()

        # Exercise Resume-only state and a different current timeline frame.
        settings = bpy.context.scene.cloth_next_recovery
        settings.resume_requested = True
        settings.recovery_directory = str(options.metadata_path.parent)
        settings.status = "Resume requested"
        settings.status_detail = "Transient integration state"
        settings.compatible = False
        settings.resumable = True
        bpy.context.scene.frame_set(7)

        resume_snapshot = solver_test.validate_scene(bpy.context)
        resumed = solver_test.build_run_plan(
            bpy.context, snapshot=resume_snapshot)
        resume_param = _payload_bytes(resumed.scene.param_payload)
        match = recovery.compatibility(
            saved.identity, resumed.recovery_options.identity)

        assert cold_param == resume_param
        assert cold.scene.param_hash == resumed.scene.param_hash
        assert match.compatible, match.reason
        assert resumed.recovery_options.resume is True

        # Real-RNA controlled invalidation: restore each field before changing
        # the next one, so every miss has exactly one artist-visible cause.
        settings.resume_requested = False
        bpy.context.scene.frame_set(1)
        invalidation = {}

        original_x = float(cloth.data.vertices[0].co.x)
        cloth.data.vertices[0].co.x = original_x + 0.125
        bpy.context.view_layer.update()
        vertex_plan = solver_test.build_run_plan(
            bpy.context, snapshot=solver_test.validate_scene(bpy.context))
        invalidation["cloth_vertex_position"] = {
            "scene_key_changed": vertex_plan.scene_cache_key != cold.scene_cache_key,
            "geometry_changed": vertex_plan.geometry_fingerprint
            != cold.geometry_fingerprint,
        }
        assert all(invalidation["cloth_vertex_position"].values())
        cloth.data.vertices[0].co.x = original_x

        original_location = float(cloth.location.x)
        cloth.location.x = original_location + 0.25
        bpy.context.view_layer.update()
        transform_plan = solver_test.build_run_plan(
            bpy.context, snapshot=solver_test.validate_scene(bpy.context))
        invalidation["object_transform"] = {
            "scene_key_changed": transform_plan.scene_cache_key != cold.scene_cache_key,
        }
        assert invalidation["object_transform"]["scene_key_changed"]
        cloth.location.x = original_location

        material = cloth.cloth_next.material
        original_stretch = float(material.stretch_resistance)
        material.stretch_resistance = original_stretch + 1.0
        material_plan = solver_test.build_run_plan(
            bpy.context, snapshot=solver_test.validate_scene(bpy.context))
        invalidation["material_setting"] = {
            "param_hash_changed": material_plan.scene.param_hash
            != cold.scene.param_hash,
        }
        assert invalidation["material_setting"]["param_hash_changed"]
        material.stretch_resistance = original_stretch

        original_end = int(cloth.cloth_next.bake_end)
        cloth.cloth_next.bake_end = original_end + 1
        frame_plan = solver_test.build_run_plan(
            bpy.context, snapshot=solver_test.validate_scene(bpy.context))
        invalidation["frame_range"] = {
            "param_hash_changed": frame_plan.scene.param_hash
            != cold.scene.param_hash,
            "identity_changed": frame_plan.recovery_options.identity.frame_end
            != options.identity.frame_end,
        }
        assert all(invalidation["frame_range"].values())
        cloth.cloth_next.bake_end = original_end

        original_fps = int(bpy.context.scene.render.fps)
        bpy.context.scene.render.fps = original_fps + 6
        fps_plan = solver_test.build_run_plan(
            bpy.context, snapshot=solver_test.validate_scene(bpy.context))
        invalidation["fps"] = {
            "param_hash_changed": fps_plan.scene.param_hash
            != cold.scene.param_hash,
            "identity_changed": fps_plan.recovery_options.identity.fps
            != options.identity.fps,
        }
        assert all(invalidation["fps"].values())
        bpy.context.scene.render.fps = original_fps

        report = {
            "result": "PASS",
            "blender_version": bpy.app.version_string,
            "solver": resolved,
            "solver_selection": {
                "before_resolution": preferences_before,
                "after_resolution": preferences_after,
            },
            "scene": {
                "cold_source_key": cold.scene_cache_key,
                "warm_source_key": warm.scene_cache_key,
                "cold_final_cache_key": cold.scene_cache_key,
                "warm_final_cache_key": warm.scene_cache_key,
                "geometry_fingerprint": cold.geometry_fingerprint,
                "topology_fingerprint": options.identity.topology_fingerprint,
                "export_uuids": list(options.identity.export_uuids),
                "cold_payload_hash": cold.scene.data_hash,
                "warm_payload_hash": warm.scene.data_hash,
                "verified_warm_hit": True,
                "warm_to_mesh_count": warm.export_timings.get(
                    "to_mesh_count", 0.0),
            },
            "recovery": {
                "stored_identity": recovery._identity_dict(saved.identity),
                "original_param_hash": cold.scene.param_hash,
                "resume_param_hash": resumed.scene.param_hash,
                "param_bytes_identical": cold_param == resume_param,
                "compatibility": match.compatible,
                "reason": match.reason,
                "resume_selected": resumed.recovery_options.resume,
                "resume_timeline_frame": 7,
            },
            "controlled_invalidation": invalidation,
        }
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("CLOTH_NEXT_IDENTITY_INTEGRATION=" + json.dumps(report))
    finally:
        solver_test.shutdown()
        registration.unregister()


if __name__ == "__main__":
    main()
