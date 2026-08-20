# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender proof for persistent constrained Character Collider reuse."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.bake.frame_range import BakeFrameRange  # noqa: E402
from cloth_next.blender import registration, solver_test  # noqa: E402


def _arguments():
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(values)


def _make_character_collider(cache_root: Path):
    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=5, y_subdivisions=5, size=2.0)
    collider = bpy.context.object
    collider.name = "Character Collider"
    collider.cloth_next.enabled = True
    collider.cloth_next.role = "COLLIDER"
    collider.cloth_next.collider_motion = "ANIMATED"
    collider.cloth_next.collider_capture_mode = "DEFORMING"
    collider.cloth_next.collider_samples_per_frame = 2
    collider.cloth_next.persistent_export_id = "character-collider-cache-smoke"

    bpy.ops.object.armature_add(enter_editmode=True)
    rig = bpy.context.object
    rig.name = "Character Rig"
    deform = rig.data.edit_bones[0]
    deform.name = "Deform"
    deform.head = (0.0, 0.0, 0.0)
    deform.tail = (0.0, 1.0, 0.0)
    control = rig.data.edit_bones.new("Control")
    control.head = (0.0, 0.0, 0.0)
    control.tail = (1.0, 0.0, 0.0)
    control.use_deform = False
    bpy.ops.object.mode_set(mode="POSE")
    constrained = rig.pose.bones["Deform"]
    constraint = constrained.constraints.new("COPY_ROTATION")
    constraint.name = "Character Copy Rotation"
    constraint.target = rig
    constraint.subtarget = "Control"
    animated = rig.pose.bones["Control"]
    animated.rotation_mode = "XYZ"
    animated.rotation_euler.z = 0.0
    animated.keyframe_insert("rotation_euler", index=2, frame=1)
    animated.rotation_euler.z = 0.6
    animated.keyframe_insert("rotation_euler", index=2, frame=3)
    bpy.ops.object.mode_set(mode="OBJECT")

    group = collider.vertex_groups.new(name="Deform")
    group.add(range(len(collider.data.vertices)), 1.0, "REPLACE")
    modifier = collider.modifiers.new("Character Armature", "ARMATURE")
    modifier.object = rig

    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=4, y_subdivisions=4, size=3.0,
        location=(0.0, 0.0, 2.0))
    cloth = bpy.context.object
    cloth.name = "Cache Owner Cloth"
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 3
    cloth.cloth_next.cache_directory = str(cache_root)
    cloth.cloth_next.persistent_export_id = "character-cache-owner"
    return cloth, collider, rig, constraint


def _lookup(cloth, collider, bake_range):
    return solver_test._load_cached_animated_colliders(
        bpy.context, SimpleNamespace(cloth_obj=cloth), (collider,), bake_range)


def main():
    args = _arguments()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.blend.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    registration.register()
    captures = []
    try:
        scene = bpy.context.scene
        scene.render.fps = 24
        scene.render.fps_base = 1.0
        scene.frame_start = 1
        scene.frame_end = 3
        scene.frame_set(1)
        cloth, collider, _rig, _constraint = _make_character_collider(args.cache)
        bake_range = BakeFrameRange(1, 3)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.blend), check_existing=False)

        hits, misses, keys, cache = _lookup(cloth, collider, bake_range)
        assert not hits and misses == (collider,)
        _cold_key, cold_reason = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        assert collider.name in keys, (
            "constrained Character Collider did not receive a safe cache key: "
            f"{cold_reason}")
        cold_key = keys[collider.name]
        cold = solver_test._capture_collider_motion(
            bpy.context, collider, bake_range)
        captures.append(cold)
        solver_test._store_animated_collider_capture(
            cache, keys[collider.name], cold)
        cold_evaluations = len(solver_test._collider_sample_points(
            bake_range, solver_test._scene_fps(bpy.context),
            collider.cloth_next.collider_samples_per_frame))
        assert cold_evaluations > 0

        bpy.ops.wm.open_mainfile(filepath=str(args.blend))
        cloth = bpy.data.objects["Cache Owner Cloth"]
        collider = bpy.data.objects["Character Collider"]
        rig = bpy.data.objects["Character Rig"]
        constraint = rig.pose.bones["Deform"].constraints[
            "Character Copy Rotation"]
        baseline_key, reason = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        assert baseline_key, reason

        warm_hits, warm_misses, _keys, cache = _lookup(
            cloth, collider, bake_range)
        assert tuple(warm_hits) == (collider.name,) and not warm_misses, (
            f"persistent warm miss: cold={cold_key}, warm={baseline_key}, "
            f"reason={reason}")

        bpy.context.scene.cloth_next_quality.quality_preset = "MEDIUM"
        quality_hits, quality_misses, _keys, _cache = _lookup(
            cloth, collider, bake_range)
        assert tuple(quality_hits) == (collider.name,) and not quality_misses

        cloth.cloth_next.material.stretch_resistance += 1.0
        material_hits, material_misses, _keys, _cache = _lookup(
            cloth, collider, bake_range)
        assert tuple(material_hits) == (collider.name,) and not material_misses

        recovery = bpy.context.scene.cloth_next_recovery
        recovery.resume_requested = True
        recovery.status = "Resume requested"
        recovery.status_detail = "Transient state"
        bpy.context.scene.frame_set(3)
        resume_hits, resume_misses, _keys, _cache = _lookup(
            cloth, collider, bake_range)
        assert tuple(resume_hits) == (collider.name,) and not resume_misses

        bpy.context.scene.frame_set(1)
        unchanged_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        assert unchanged_key == baseline_key

        original_x = collider.data.vertices[0].co.x
        collider.data.vertices[0].co.x = original_x + 0.125
        mesh_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        collider.data.vertices[0].co.x = original_x

        curve = next(curve for curve in solver_test._action_curves_for_owner(
            rig, rig.animation_data.action)[0]
            if curve.data_path == 'pose.bones["Control"].rotation_euler'
            and curve.array_index == 2)
        point = curve.keyframe_points[-1]
        original_animation = point.co.y
        point.co.y = original_animation + 0.25
        animation_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        point.co.y = original_animation

        original_influence = constraint.influence
        constraint.influence = 0.5
        constraint_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        constraint.influence = original_influence

        start_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, BakeFrameRange(2, 3))
        end_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, BakeFrameRange(1, 4))
        bpy.context.scene.render.fps = 30
        fps_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        bpy.context.scene.render.fps = 24
        collider.cloth_next.collider_samples_per_frame = 4
        samples_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        collider.cloth_next.collider_samples_per_frame = 2
        collider.cloth_next.collider_capture_mode = "AUTO"
        mode_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)
        collider.cloth_next.collider_capture_mode = "DEFORMING"

        payload = cache.lookup("collider", baseline_key).path
        payload.write_bytes(payload.read_bytes() + b"corrupt")
        corrupt_capture, _reason = solver_test._load_animated_collider_capture(
            cache, baseline_key)

        collider.data.vertices.add(1)
        topology_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, collider, bake_range)

        bpy.ops.mesh.primitive_grid_add(
            x_subdivisions=3, y_subdivisions=3, size=1.0)
        shape_collider = bpy.context.object
        shape_collider.name = "Shape Key Collider"
        shape_collider.cloth_next.enabled = True
        shape_collider.cloth_next.role = "COLLIDER"
        shape_collider.cloth_next.collider_motion = "ANIMATED"
        shape_collider.cloth_next.collider_capture_mode = "DEFORMING"
        shape_collider.cloth_next.collider_samples_per_frame = 2
        shape_collider.cloth_next.persistent_export_id = "shape-key-smoke"
        shape_collider.shape_key_add(name="Basis")
        shape = shape_collider.shape_key_add(name="Deform")
        shape.data[0].co.z += 0.5
        shape.value = 0.0
        shape.keyframe_insert("value", frame=1)
        shape.value = 1.0
        shape.keyframe_insert("value", frame=3)
        bpy.context.scene.frame_set(1)
        shape_key, shape_reason = solver_test._animated_collider_cache_key(
            bpy.context, shape_collider, bake_range)
        assert shape_key, shape_reason
        bpy.context.scene.frame_set(3)
        shape_frame_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, shape_collider, bake_range)
        shape_action = shape_collider.data.shape_keys.animation_data.action
        shape_curve = next(iter(solver_test._action_curves_for_owner(
            shape_collider.data.shape_keys, shape_action)[0]))
        shape_point = shape_curve.keyframe_points[-1]
        shape_point.co.y = 0.75
        shape_changed_key, _ = solver_test._animated_collider_cache_key(
            bpy.context, shape_collider, bake_range)

        invalidation = {
            "mesh": mesh_key != baseline_key,
            "topology": topology_key != baseline_key,
            "animation": animation_key != baseline_key,
            "shape_key": (shape_frame_key == shape_key
                          and shape_changed_key != shape_key),
            "constraint": constraint_key != baseline_key,
            "bake_start": start_key != baseline_key,
            "bake_end": end_key != baseline_key,
            "fps": fps_key != baseline_key,
            "samples": samples_key != baseline_key,
            "mode": mode_key != baseline_key,
            "corrupt_cache": corrupt_capture is None,
        }
        assert all(invalidation.values()), invalidation
        report = {
            "cold_evaluations": cold_evaluations,
            "unchanged_evaluations": 0,
            "quality_evaluations": 0,
            "material_evaluations": 0,
            "resume_evaluations": 0,
            "persistent_reload": True,
            "invalidation": invalidation,
        }
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
    finally:
        for capture in captures:
            capture.cleanup()
        registration.unregister()


if __name__ == "__main__":
    main()
