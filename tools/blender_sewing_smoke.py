# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender smoke test for path Sewing registration and export snapshots."""

import sys
import os
from pathlib import Path
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import sewing
from cloth_next.attachments import topology_fingerprint
from cloth_next.attachments import wire_entry


def cloth_object(name, offset=0.0):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(((0, 0, 0), (1, 0, 0), (2, 0, 0),
                      (0, 1, 0), (1, 1, 0), (2, 1, 0)),
                     ((0, 1), (1, 2), (3, 4), (4, 5)),
                     ((0, 1, 4, 3), (1, 2, 5, 4)))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location.x = offset
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.clothnext.add_physics()
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
    assert intra_pairs == {}
    assert len(cross_records) == 2
    assert all(len(record[0].points) == 3 for record in cross_records)
    assert all(record[0].stiffness == 50_000.0 for record in cross_records)
    for attachment, source_count, target_count in cross_records:
        payload = wire_entry(attachment, source_vertex_count=source_count,
                             target_vertex_count=target_count)
        assert payload["stitch_stiffness"] == 50_000.0
    intra.strength = 25.0
    cross.strength = 0.0
    _, records = sewing.snapshot_enabled(bpy.context.scene, (entry(first), entry(second)))
    assert len(records) == 1 and records[0][0].stiffness == 12_500.0
    configured_solver = os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE")
    if configured_solver:
        from cloth_next.blender import solver_test
        from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
        from cloth_next.ppf.schema import envelope
        resolved = SolverResolver(lambda _path: ("0.1.0", "0.22", "2")).resolve(
            SolverResolutionContext(development_executable=Path(configured_solver)))
        solver_test.resolve_solver = lambda _context: resolved
        cross.enabled = False
        second.cloth_next.enabled = False
        first.cloth_next.cache_directory = bpy.app.tempdir
        first.cloth_next.bake_end = 13
        bpy.context.view_layer.objects.active = first
        first.select_set(True)
        plan = solver_test.build_run_plan(bpy.context)
        payload = envelope.loads_envelope(plan.scene.param_payload, envelope.KIND_PARAM,
                                          schema_version=2)
        assert len(payload["cross_stitch"]) == 1
        seam = payload["cross_stitch"][0]
        assert seam["source_uuid"] == seam["target_uuid"]
        assert seam["stitch_stiffness"] == 12_500.0
        assert not payload["pin_config"]
    bpy.context.scene.cloth_next_show_sewing = False
    assert bpy.context.scene.cloth_next_show_sewing is False
    cloth_next.unregister()
    assert not hasattr(bpy.types.Scene, "cloth_next_sewing_definitions")
    print("Sewing registration, dynamic intra/cross mapping and snapshot smoke passed")


main()
