# SPDX-License-Identifier: GPL-3.0-or-later

"""Modifier validation for supported animated-pin workflows."""

from types import SimpleNamespace

import pytest


def _pin_membership(enabled):
    return SimpleNamespace(enabled=enabled)


def test_armature_without_cloth_next_pinning_explains_follow_animation(
        blender_env):
    obj = blender_env.bpy.types.Object(name="Rigged Cloth", type="MESH")
    obj.modifiers.new("Armature", "ARMATURE")

    with pytest.raises(blender_env.solver_test.SceneValidationError) as caught:
        blender_env.solver_test._validate_deformable_modifier_path(
            obj, _pin_membership(False))

    message = str(caught.value)
    assert "Cloth NeXt Pinning is disabled" in message
    assert "select the animated Pin Group" in message
    assert "Pin Mode to Follow Animation" in message


def test_armature_is_allowed_when_cloth_next_pinning_is_enabled(blender_env):
    obj = blender_env.bpy.types.Object(name="Rigged Cloth", type="MESH")
    obj.modifiers.new("Armature", "ARMATURE")

    blender_env.solver_test._validate_deformable_modifier_path(
        obj, _pin_membership(True))


def test_other_modifier_keeps_plain_mesh_guidance(blender_env):
    obj = blender_env.bpy.types.Object(name="Subdivided Cloth", type="MESH")
    obj.modifiers.new("Subdivision", "SUBSURF")

    with pytest.raises(blender_env.solver_test.SceneValidationError) as caught:
        blender_env.solver_test._validate_deformable_modifier_path(
            obj, _pin_membership(False))

    assert "requires a plain mesh" in str(caught.value)


def test_render_only_subdivision_does_not_block_viewport_bake(blender_env):
    obj = blender_env.bpy.types.Object(name="Render Smooth Cloth", type="MESH")
    modifier = obj.modifiers.new("Render Subdivision", "SUBSURF")
    modifier.show_viewport = False
    modifier.show_render = True

    blender_env.solver_test._validate_deformable_modifier_path(
        obj, _pin_membership(False))


def test_viewport_subdivision_still_requires_supported_workflow(blender_env):
    obj = blender_env.bpy.types.Object(name="Viewport Subdiv Cloth", type="MESH")
    modifier = obj.modifiers.new("Viewport Subdivision", "SUBSURF")
    modifier.show_viewport = True

    with pytest.raises(blender_env.solver_test.SceneValidationError,
                       match="requires a plain mesh"):
        blender_env.solver_test._validate_deformable_modifier_path(
            obj, _pin_membership(False))
