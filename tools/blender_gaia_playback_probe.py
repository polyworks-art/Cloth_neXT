# SPDX-License-Identifier: GPL-3.0-or-later
"""Evaluate an actual CUDA-produced PC2 with Blender's Mesh Cache modifier."""
import argparse
import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.bake import pc2
from cloth_next.blender import registration, solver_test
from cloth_next.ppf_run import fixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    header = pc2.read_header(args.cache)
    expected = np.frombuffer(args.cache.read_bytes()[pc2.PC2_HEADER_SIZE:],
                             dtype="<f4").reshape(header.frame_count, header.vertex_count, 3)
    cloth, _ = fixture.vertical_slice_fixture()
    assert header.vertex_count == len(cloth.vertices_local)
    registration.register()
    try:
        mesh = bpy.data.meshes.new("GAIA lifecycle playback")
        mesh.from_pydata(cloth.vertices_local, [], cloth.triangles)
        obj = bpy.data.objects.new("GAIA lifecycle playback", mesh)
        bpy.context.collection.objects.link(obj)
        modifier = obj.modifiers.new("Cloth NeXt Cache", "MESH_CACHE")
        solver_test._configure_playback_modifier(modifier, 1)
        modifier.filepath = str(args.cache.resolve())
        tested = sorted({1, 2, header.frame_count})
        for frame in tested:
            bpy.context.scene.frame_set(frame)
            bpy.context.view_layer.update()
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            actual = np.asarray([tuple(vertex.co) for vertex in evaluated.data.vertices])
            np.testing.assert_allclose(actual, expected[frame-1], atol=1e-6, rtol=1e-6)
            del evaluated
        modifier.filepath = ""
        obj.modifiers.remove(modifier)
        solver_test._release_playback_readers()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        bpy.context.view_layer.update()
        args.report.write_text(json.dumps({"result": "PASS", "frames": tested,
                                          "cache": str(args.cache), "modifier_removed": True}, indent=2))
    finally:
        registration.unregister()


if __name__ == "__main__":
    main()
