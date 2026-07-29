# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender smoke for rigid and deforming Collider capture."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
import tracemalloc

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.bake.frame_range import BakeFrameRange  # noqa: E402
from cloth_next.blender import registration, solver_test  # noqa: E402
from cloth_next.ppf.schema.data import SceneObject  # noqa: E402
from cloth_next.ppf_run.session import SessionCancelled  # noqa: E402


def _capture(obj, start=1, end=3):
    original = bpy.context.scene.frame_current
    tracemalloc.start()
    began = time.perf_counter()
    capture = solver_test._capture_collider_motion(
        bpy.context, obj, BakeFrameRange(start, end))
    elapsed = time.perf_counter() - began
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert bpy.context.scene.frame_current == original
    return capture, elapsed, peak


def main():
    registration.register()
    scene = bpy.context.scene
    scene.render.fps = 30
    scene.frame_set(2)

    bpy.ops.mesh.primitive_cube_add()
    rigid = bpy.context.object
    rigid.name = "RigidColliderSmoke"
    rigid.cloth_next.enabled = True
    rigid.cloth_next.role = "COLLIDER"
    rigid.cloth_next.collider_motion = "ANIMATED"
    rigid.cloth_next.collider_samples_per_frame = 8
    rigid.location.x = 0.0
    rigid.keyframe_insert("location", frame=1)
    rigid.location.x = 2.0
    rigid.rotation_euler.z = 0.7
    rigid.scale = (1.0, 2.0, 0.5)
    rigid.keyframe_insert("location", frame=64)
    rigid.keyframe_insert("rotation_euler", frame=64)
    rigid.keyframe_insert("scale", frame=64)
    bake_range = BakeFrameRange(1, 64)
    rigid_capture, rigid_seconds, rigid_peak = _capture(rigid, 1, 64)
    assert rigid_capture.motion_type == "RIGID_ANIMATED"
    assert len(rigid_capture.animation["time"]) == 505
    assert abs(rigid_capture.animation["time"][-1] - 2.1) < 1e-12
    assert rigid_capture.animation["_logical_frame_count"] == 64
    assert rigid_capture.animation["_sample_frame_offset"][1] == 0.125
    assert rigid_capture.animation["_sample_frame_offset"][8] == 1.0
    encoded = SceneObject(
        rigid.name, "rigid-smoke", rigid_capture.vertices,
        rigid_capture.triangles, rigid_capture.transform,
        transform_animation=rigid_capture.animation).info_dict(
            schema_version=2)["transform_animation"]
    assert encoded["frame_offset"] == list(range(64))
    assert len(encoded["translation"]) == 64
    assert encoded["translation"][1] != encoded["translation"][0]
    assert encoded["translation"][-1] != encoded["translation"][0]
    playback_frames = [
        bake_range.blender_frame(step)
        for step in range(bake_range.output_count)]
    assert bake_range.solver_steps == 63
    assert bake_range.output_count == 64
    assert playback_frames == list(range(1, 65))
    rigid_capture.cleanup()

    bpy.ops.mesh.primitive_cube_add()
    deform = bpy.context.object
    deform.name = "ShapeKeyColliderSmoke"
    deform.cloth_next.enabled = True
    deform.cloth_next.role = "COLLIDER"
    deform.cloth_next.collider_motion = "ANIMATED"
    deform.cloth_next.collider_samples_per_frame = 2
    deform.shape_key_add(name="Basis")
    key = deform.shape_key_add(name="Raise")
    key.data[0].co.z += 1.0
    key.value = 0.0
    key.keyframe_insert("value", frame=1)
    key.value = 1.0
    key.keyframe_insert("value", frame=3)
    deform.location.y = 1.0
    deform.keyframe_insert("location", frame=1)
    deform.location.y = 2.0
    deform.keyframe_insert("location", frame=3)
    deform_capture, deform_seconds, deform_peak = _capture(deform)
    assert deform_capture.motion_type == "DEFORMING_ANIMATED"
    temp_size = deform_capture.temporary_path.stat().st_size
    assert temp_size == 5 * len(deform.data.vertices) * 3 * 4
    deform_capture.cleanup()

    bpy.ops.mesh.primitive_cube_add()
    changing = bpy.context.object
    changing.name = "TopologyChangingColliderSmoke"
    modifier = changing.modifiers.new("Animated Array", "ARRAY")
    modifier.driver_add("count").driver.expression = "1 if frame < 3 else 2"
    topology_rejected = False
    try:
        _capture(changing)
    except solver_test.SceneValidationError as exc:
        topology_rejected = (changing.name in str(exc)
                             and "frame 3" in str(exc))
    assert topology_rejected

    scene.frame_set(2)
    solver_test._cancel_event.set()
    try:
        _capture(deform)
    except SessionCancelled:
        pass
    else:
        raise AssertionError("pre-capture cancellation was ignored")
    finally:
        solver_test._cancel_event.clear()
    assert scene.frame_current == 2

    for obj in (rigid, deform):
        assert not any(mod.type == "MESH_CACHE" for mod in obj.modifiers)

    print(json.dumps({
        "frames": 3,
        "colliders": 2,
        "collider_vertices_total": (len(rigid.data.vertices)
                                    + len(deform.data.vertices)),
        "rigid_capture_seconds": rigid_seconds,
        "deforming_capture_seconds": deform_seconds,
        "rigid_samples": 505,
        "deforming_samples": 5,
        "logical_playback_frames": 64,
        "average_capture_seconds_per_sample": (
            rigid_seconds + deform_seconds) / 510.0,
        "temporary_file_bytes": temp_size,
        "python_peak_bytes": max(rigid_peak, deform_peak),
        "frame_restored": scene.frame_current == 2,
        "collider_mesh_cache_modifiers": 0,
        "topology_change_rejected": topology_rejected,
        "pre_capture_cancelled": True,
    }, sort_keys=True))
    registration.unregister()


if __name__ == "__main__":
    main()
