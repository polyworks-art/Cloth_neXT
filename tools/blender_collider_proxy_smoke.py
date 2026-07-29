# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender smoke tests for both animated Collider proxy modes."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.blender import collider_proxy, registration  # noqa: E402


def _simple_proxy_smoke(scene):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32,
                                         location=(4.0, 0.0, 0.0))
    source = bpy.context.object
    source.name = "DenseProxySourceSmoke"
    source.cloth_next.enabled = True
    source.cloth_next.role = "COLLIDER"
    source.cloth_next.collider_motion = "ANIMATED"
    source.cloth_next.collider_proxy_type = "SIMPLE"
    source.cloth_next.collider_proxy_target_vertices = 500
    bend = source.modifiers.new("Animated Bend", "SIMPLE_DEFORM")
    bend.deform_method = "BEND"
    bend.angle = 0.0
    bend.keyframe_insert("angle", frame=1)
    bend.angle = 0.5
    bend.keyframe_insert("angle", frame=3)

    source_vertices = len(source.data.vertices)
    proxy = collider_proxy.generate_proxy(bpy.context, source)
    proxy_vertices = len(proxy.data.vertices)
    assert proxy_vertices < source_vertices
    assert collider_proxy.resolve_proxy(source) is proxy
    assert any(modifier.type == "SIMPLE_DEFORM"
               for modifier in proxy.modifiers)

    positions = []
    for frame in (1, 3):
        scene.frame_set(frame)
        evaluated = proxy.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            positions.append(tuple(mesh.vertices[0].co))
            assert len(mesh.vertices) == proxy_vertices
        finally:
            evaluated.to_mesh_clear()
    assert positions[0] != positions[1]
    return source_vertices, proxy_vertices


def _build_character():
    bpy.ops.object.armature_add(enter_editmode=True, location=(0.0, 0.0, 0.0))
    armature = bpy.context.object
    armature.name = "CharacterCageRigSmoke"
    edit_root = armature.data.edit_bones[0]
    edit_root.name = "pelvis"
    edit_root.head = (0.0, 0.0, -1.0)
    edit_root.tail = (0.0, 0.0, 0.0)
    edit_child = armature.data.edit_bones.new("spine")
    edit_child.head = (0.0, 0.0, 0.0)
    edit_child.tail = (0.0, 0.0, 1.0)
    edit_child.parent = edit_root
    edit_child.use_connect = True
    bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16)
    body = bpy.context.object
    body.name = "CharacterCageBodySmoke"
    body.scale = (0.55, 0.38, 1.2)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    pelvis = body.vertex_groups.new(name="pelvis")
    spine = body.vertex_groups.new(name="spine")
    pelvis_indices = []
    spine_indices = []
    for vertex in body.data.vertices:
        if vertex.co.z <= 0.25:
            pelvis_indices.append(vertex.index)
        if vertex.co.z >= -0.25:
            spine_indices.append(vertex.index)
    pelvis.add(pelvis_indices, 1.0, "REPLACE")
    spine.add(spine_indices, 1.0, "REPLACE")

    modifier = body.modifiers.new("Character Rig", "ARMATURE")
    modifier.object = armature
    pose_bone = armature.pose.bones["spine"]
    pose_bone.rotation_mode = "XYZ"
    pose_bone.rotation_euler = (0.0, 0.0, 0.0)
    pose_bone.keyframe_insert("rotation_euler", frame=1)
    pose_bone.rotation_euler = (math.radians(28.0), 0.0, 0.0)
    pose_bone.keyframe_insert("rotation_euler", frame=3)
    return body, armature


def _character_cage_smoke(scene):
    body, _armature = _build_character()
    settings = body.cloth_next
    settings.enabled = True
    settings.role = "COLLIDER"
    settings.collider_motion = "ANIMATED"
    settings.collider_proxy_type = "CHARACTER_CAGE"
    settings.bake_start = 1
    settings.bake_end = 3
    settings.collider_cage_sample_step = 1
    settings.collider_cage_min_vertices = 4
    settings.collider_cage_margin = 0.002
    settings.collider_cage_joint_overlap = 0.01

    primary = collider_proxy.generate_proxy(bpy.context, body)
    segments = collider_proxy.character_collision_cage.owned_cage_segments(body)
    assert len(segments) >= 2
    assert collider_proxy.resolve_proxy(body) is primary
    assert all(segment.cloth_next.collider_capture_mode == "TRANSFORM_ONLY"
               for segment in segments)
    assert all(segment.cloth_next.collider_proxy_source is body
               for segment in segments)
    assert sum(len(segment.data.vertices) for segment in segments) == \
        settings.collider_proxy_result_vertices

    spine = next(segment for segment in segments
                 if segment.get(
                     collider_proxy.character_collision_cage.CAGE_BONE) == "spine")
    transforms = []
    for frame in (1, 3):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        transforms.append(tuple(tuple(float(value) for value in row)
                                for row in spine.matrix_world))
    assert transforms[0] != transforms[1]
    return len(body.data.vertices), len(segments), \
        settings.collider_proxy_result_vertices


def main():
    registration.register()
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 3

    simple_source, simple_proxy = _simple_proxy_smoke(scene)
    cage_source, cage_segments, cage_vertices = _character_cage_smoke(scene)

    print(json.dumps({
        "simple_source_vertices": simple_source,
        "simple_proxy_vertices": simple_proxy,
        "character_source_vertices": cage_source,
        "character_cage_segments": cage_segments,
        "character_cage_vertices": cage_vertices,
        "animated": True,
    }), flush=True)
    registration.unregister()


if __name__ == "__main__":
    main()
