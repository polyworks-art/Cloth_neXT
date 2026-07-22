# SPDX-License-Identifier: GPL-3.0-or-later

"""Deformable modifiers are accepted independently of export geometry."""

import sys
from types import ModuleType, SimpleNamespace

from tests import mesh_fixtures

def _pin_membership(enabled):
    return SimpleNamespace(enabled=enabled)


def test_self_intersection_check_deduplicates_pairs_and_ignores_neighbours(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    fake_mathutils = ModuleType("mathutils")
    fake_bvhtree = ModuleType("mathutils.bvhtree")

    class FakeTree:
        def overlap(self, _other):
            return [(0, 0), (0, 1), (1, 0), (0, 2), (2, 0)]

    class FakeBVH:
        @staticmethod
        def FromPolygons(_vertices, _triangles, all_triangles=False):
            assert all_triangles
            return FakeTree()

    fake_bvhtree.BVHTree = FakeBVH
    monkeypatch.setitem(sys.modules, "mathutils", fake_mathutils)
    monkeypatch.setitem(sys.modules, "mathutils.bvhtree", fake_bvhtree)
    triangles = ((0, 1, 2), (2, 1, 3), (4, 5, 6))

    count, vertices = module._self_intersection_vertices(
        ((0.0, 0.0, 0.0),) * 7, triangles)

    assert count == 1
    assert vertices == (0, 1, 2, 4, 5, 6)


def test_armature_without_cloth_next_pinning_is_allowed(blender_env):
    obj = blender_env.bpy.types.Object(name="Rigged Cloth", type="MESH")
    obj.modifiers.new("Armature", "ARMATURE")

    blender_env.solver_test._validate_deformable_modifier_path(
        obj, _pin_membership(False))


def test_armature_is_allowed_when_cloth_next_pinning_is_enabled(blender_env):
    obj = blender_env.bpy.types.Object(name="Rigged Cloth", type="MESH")
    obj.modifiers.new("Armature", "ARMATURE")

    blender_env.solver_test._validate_deformable_modifier_path(
        obj, _pin_membership(True))


def test_topology_changing_modifier_without_pinning_is_allowed(blender_env):
    obj = blender_env.bpy.types.Object(name="Subdivided Cloth", type="MESH")
    obj.modifiers.new("Subdivision", "SUBSURF")

    blender_env.solver_test._validate_deformable_modifier_path(
        obj, _pin_membership(False))


def test_render_only_subdivision_does_not_block_viewport_bake(blender_env):
    obj = blender_env.bpy.types.Object(name="Render Smooth Cloth", type="MESH")
    modifier = obj.modifiers.new("Render Subdivision", "SUBSURF")
    modifier.show_viewport = False
    modifier.show_render = True

    blender_env.solver_test._validate_deformable_modifier_path(
        obj, _pin_membership(False))


def test_viewport_subdivision_is_downstream_and_allowed(blender_env):
    obj = blender_env.bpy.types.Object(name="Viewport Subdiv Cloth", type="MESH")
    modifier = obj.modifiers.new("Viewport Subdivision", "SUBSURF")
    modifier.show_viewport = True

    blender_env.solver_test._validate_deformable_modifier_path(
        obj, _pin_membership(False))


def test_deformable_export_reads_source_mesh_without_evaluating_modifiers(
        blender_env):
    blender_env.registration.register()
    scene = mesh_fixtures.build_cloth_scene(blender_env.bpy, vertex_count=16)
    scene.cloth.modifiers.new("Topology Change", "SUBSURF")
    scene.cloth.evaluated_get = lambda _depsgraph: (_ for _ in ()).throw(
        AssertionError("deformable modifiers must not be evaluated for export"))

    vertices, triangles = blender_env.solver_test._extract_source_mesh(
        scene.cloth, needs_edges=True)

    assert len(vertices) == len(scene.cloth.data.vertices)
    assert triangles
    blender_env.registration.unregister()
