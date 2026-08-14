# SPDX-License-Identifier: GPL-3.0-or-later

"""Solver-input modifier boundary and pinning validation regressions."""

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


def test_solver_input_export_disables_only_modifiers_after_boundary(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Rigged Cloth", type="MESH")
    rig = obj.modifiers.new("Armature", "ARMATURE")
    smooth = obj.modifiers.new("Corrective Smooth", "CORRECTIVE_SMOOTH")
    after = obj.modifiers.new("After Rig", "SOLIDIFY")
    rig.show_viewport = smooth.show_viewport = after.show_viewport = True
    updates = []
    monkeypatch.setattr(module, "_depsgraph_update",
                        lambda _context: updates.append(True))

    with module._evaluate_through_solver_input_modifiers(
            SimpleNamespace(), obj) as evaluated:
        assert evaluated
        assert rig.show_viewport
        assert smooth.show_viewport
        assert not after.show_viewport

    assert after.show_viewport
    assert len(updates) == 2


def test_disabled_armature_keeps_source_mesh_export_path(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Disabled Rig", type="MESH")
    rig = obj.modifiers.new("Armature", "ARMATURE")
    rig.show_viewport = False
    after = obj.modifiers.new("After Rig", "SUBSURF")
    after.show_viewport = True
    monkeypatch.setattr(module, "_depsgraph_update",
                        lambda _context: (_ for _ in ()).throw(
                            AssertionError("depsgraph must stay untouched")))

    with module._evaluate_through_solver_input_modifiers(
            SimpleNamespace(), obj) as evaluated:
        assert not evaluated
        assert after.show_viewport


def test_corrective_smooth_without_armature_is_solver_input(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Smoothed Cloth", type="MESH")
    smooth = obj.modifiers.new("Corrective Smooth", "CORRECTIVE_SMOOTH")
    downstream = obj.modifiers.new("Subdivision", "SUBSURF")
    smooth.show_viewport = downstream.show_viewport = True
    monkeypatch.setattr(module, "_depsgraph_update", lambda _context: None)

    with module._evaluate_through_solver_input_modifiers(
            SimpleNamespace(), obj) as evaluated:
        assert evaluated
        assert smooth.show_viewport
        assert not downstream.show_viewport

    assert downstream.show_viewport


def test_disabled_corrective_smooth_is_ignored(blender_env, monkeypatch):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Disabled Smooth", type="MESH")
    smooth = obj.modifiers.new("Corrective Smooth", "CORRECTIVE_SMOOTH")
    smooth.show_viewport = False
    monkeypatch.setattr(module, "_depsgraph_update", lambda _context: (_ for _ in ()).throw(
        AssertionError("depsgraph must stay untouched")))

    with module._evaluate_through_solver_input_modifiers(
            SimpleNamespace(), obj) as evaluated:
        assert not evaluated
        assert not smooth.show_viewport


def test_supported_modifier_after_topology_modifier_is_rejected(blender_env):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Unsafe Cloth", type="MESH")
    obj.modifiers.new("Armature", "ARMATURE")
    obj.modifiers.new("Subdivision", "SUBSURF")
    obj.modifiers.new("Corrective Smooth", "CORRECTIVE_SMOOTH")

    import pytest
    with pytest.raises(module.SceneValidationError, match="cannot be included"):
        module._validate_deformable_modifier_path(obj, _pin_membership(False))


def test_solver_input_visibility_restored_after_exception(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Rigged Cloth", type="MESH")
    obj.modifiers.new("Armature", "ARMATURE").show_viewport = True
    downstream = obj.modifiers.new("Subdivision", "SUBSURF")
    downstream.show_viewport = True
    monkeypatch.setattr(module, "_depsgraph_update", lambda _context: None)

    import pytest
    with pytest.raises(RuntimeError):
        with module._evaluate_through_solver_input_modifiers(
                SimpleNamespace(), obj):
            assert not downstream.show_viewport
            raise RuntimeError("capture failed")
    assert downstream.show_viewport
