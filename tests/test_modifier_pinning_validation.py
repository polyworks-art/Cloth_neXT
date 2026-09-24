# SPDX-License-Identifier: GPL-3.0-or-later

"""Solver-input modifier boundary and pinning validation regressions."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from tests import mesh_fixtures

def _pin_membership(enabled):
    return SimpleNamespace(enabled=enabled)


def test_simulation_boundary_is_created_once_and_ignores_artist_cache(
        blender_env):
    cache = sys.modules["cloth_next.blender.playback_cache"]
    obj = blender_env.bpy.types.Object(name="Cloth", type="MESH")
    artist = obj.modifiers.new("Artist Cache", "MESH_CACHE")

    boundary = cache.ensure_simulation_modifier(obj)
    again = cache.ensure_simulation_modifier(obj)

    assert boundary is again
    assert tuple(obj.modifiers) == (artist, boundary)
    assert boundary.name == "Cloth NeXt"
    assert boundary.show_viewport is False
    assert boundary.show_render is False
    assert cache.simulation_modifiers(obj) == (boundary,)


def test_renamed_simulation_boundary_keeps_stable_identity(blender_env):
    cache = sys.modules["cloth_next.blender.playback_cache"]
    obj = blender_env.bpy.types.Object(name="Cloth", type="MESH")
    boundary = cache.ensure_simulation_modifier(obj)

    boundary.name = "My Simulation Point"

    assert cache.simulation_modifiers(obj) == (boundary,)
    assert cache.ensure_simulation_modifier(obj) is boundary


def test_explicit_boundary_uses_isolated_prefix_without_mutating_user_object(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    cache = sys.modules["cloth_next.blender.playback_cache"]
    obj = blender_env.bpy.types.Object(name="Cloth", type="MESH")
    before = obj.modifiers.new("Subdivision", "SUBSURF")
    boundary = cache.ensure_simulation_modifier(obj)
    after = obj.modifiers.new("Post Smooth", "SMOOTH")
    before.show_viewport = boundary.show_viewport = after.show_viewport = True
    monkeypatch.setattr(module, "_depsgraph_update", lambda _context: None)

    with module._evaluate_through_solver_input_modifiers(
            SimpleNamespace(), obj) as evaluated:
        assert evaluated is not obj
        assert [modifier.name for modifier in evaluated.modifiers] == [
            "Subdivision"]
        assert before.show_viewport
        assert boundary.show_viewport
        assert after.show_viewport

    assert before.show_viewport
    assert boundary.show_viewport
    assert after.show_viewport


def test_enabled_deformable_without_boundary_has_controlled_error(blender_env):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Cloth", type="MESH")
    obj.cloth_next = SimpleNamespace(enabled=True, role="CLOTH")

    with pytest.raises(module.SceneValidationError, match="modifier is missing"):
        module._solver_input_modifier_cutoff(obj)


def test_duplicate_boundaries_have_controlled_error(blender_env):
    module = blender_env.solver_test
    cache = sys.modules["cloth_next.blender.playback_cache"]
    obj = blender_env.bpy.types.Object(name="Cloth", type="MESH")
    first = obj.modifiers.new("Cloth NeXt", "MESH_CACHE")
    second = obj.modifiers.new("Cloth NeXt Copy", "MESH_CACHE")
    cache.mark_simulation_modifier(obj, first)
    cache.mark_simulation_modifier(obj, second)

    with pytest.raises(module.SceneValidationError, match="multiple Cloth NeXt"):
        module._solver_input_modifier_cutoff(obj)


def test_animated_topology_mismatch_is_rejected_and_frame_restored(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    cache = sys.modules["cloth_next.blender.playback_cache"]
    obj = blender_env.bpy.types.Object(name="Animated Cloth", type="MESH")
    obj.modifiers.new("Animated Subdivision", "SUBSURF").show_viewport = True
    cache.ensure_simulation_modifier(obj)
    scene = SimpleNamespace(frame_current=7, frame_subframe=0.25)

    def frame_set(frame, subframe=0.0):
        scene.frame_current = int(frame)
        scene.frame_subframe = float(subframe)

    scene.frame_set = frame_set
    context = SimpleNamespace(scene=scene)
    monkeypatch.setattr(
        module, "_evaluated_deformable_signatures",
        lambda _context, _obj: (
            "changed" if scene.frame_current == 5 else "stable", "shape", 4, 2))

    with pytest.raises(module.SceneValidationError,
                       match="topology changes.*frame 5"):
        module._validate_boundary_topology_range(
            context, obj, SimpleNamespace(start=1, end=9), "stable")

    assert scene.frame_current == 7
    assert scene.frame_subframe == 0.25


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

    vertices = (
        (0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (0.0, 2.0, 0.0),
        (2.0, 2.0, 0.0), (0.5, 0.5, -1.0),
        (0.5, 0.5, 1.0), (1.5, 0.5, 0.0))

    count, vertices = module._self_intersection_vertices(vertices, triangles)

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
        assert evaluated is not obj
        assert rig.show_viewport
        assert smooth.show_viewport
        assert after.show_viewport
        assert [modifier.name for modifier in evaluated.modifiers] == [
            "Armature", "Corrective Smooth"]

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
        assert evaluated is not obj
        assert smooth.show_viewport
        assert downstream.show_viewport
        assert [modifier.name for modifier in evaluated.modifiers] == [
            "Corrective Smooth"]

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
                SimpleNamespace(), obj) as evaluated:
            assert evaluated is not obj
            assert downstream.show_viewport
            assert [modifier.name for modifier in evaluated.modifiers] == [
                "Armature"]
            raise RuntimeError("capture failed")
    assert downstream.show_viewport
