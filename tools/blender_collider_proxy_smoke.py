# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender smoke test for the experimental animated Collider proxy."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.blender import collider_proxy, registration  # noqa: E402


def main():
    registration.register()
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 3

    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32)
    source = bpy.context.object
    source.name = "DenseProxySourceSmoke"
    source.cloth_next.enabled = True
    source.cloth_next.role = "COLLIDER"
    source.cloth_next.collider_motion = "ANIMATED"
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

    print(json.dumps({"source_vertices": source_vertices,
                      "proxy_vertices": proxy_vertices,
                      "animated": True}), flush=True)
    registration.unregister()


if __name__ == "__main__":
    main()
