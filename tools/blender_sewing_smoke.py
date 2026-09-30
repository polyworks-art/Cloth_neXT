# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender smoke test for path Sewing registration and export snapshots."""

import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import sewing
from cloth_next.attachments import topology_fingerprint


def cloth_object(name, offset=0.0):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(((0, 0, 0), (1, 0, 0), (2, 0, 0),
                      (0, 1, 0), (1, 1, 0), (2, 1, 0)),
                     ((0, 1), (1, 2), (3, 4), (4, 5)),
                     ((0, 1, 4, 3), (1, 2, 5, 4)))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location.x = offset
    obj.cloth_next.enabled = True
    obj.cloth_next.role = "CLOTH"
    return obj


def entry(obj):
    vertices, _edges, triangles = sewing._mesh(obj)
    return SimpleNamespace(obj=obj, role="CLOTH", boundary_vertices=vertices,
                           boundary_triangles=triangles,
                           topology_signature=topology_fingerprint(len(vertices), triangles))


def main():
    cloth_next.register()
    bpy.data.objects.remove(bpy.context.active_object, do_unlink=True)
    first, second = cloth_object("Panel A"), cloth_object("Panel B", 3.0)
    intra = sewing._commit(bpy.context.scene, first, first, (0, 1, 2), (3, 4, 5))
    cross = sewing._commit(bpy.context.scene, first, second, (0, 1, 2), (3, 4, 5))
    assert len(intra.mapping) == len(cross.mapping) == 3
    assert first.cloth_next.pressure.sewing_stiffness == 100.0
    assert cross.strength == 100.0
    intra_pairs, cross_records = sewing.snapshot_enabled(
        bpy.context.scene, (entry(first), entry(second)))
    assert len(intra_pairs[first.cloth_next.persistent_export_id]) == 3
    assert len(cross_records) == 1 and len(cross_records[0][0].points) == 3
    bpy.context.scene.cloth_next_show_sewing = False
    assert bpy.context.scene.cloth_next_show_sewing is False
    cloth_next.unregister()
    assert not hasattr(bpy.types.Scene, "cloth_next_sewing_definitions")
    print("Sewing registration, intra/cross mapping and snapshot smoke passed")


main()
