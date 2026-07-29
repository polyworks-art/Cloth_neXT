# SPDX-License-Identifier: GPL-3.0-or-later

import pytest


def test_character_cage_frame_sampling_includes_end(blender_env):
    cage = blender_env.collider_proxy.character_collision_cage
    assert cage.sample_frames(1, 10, 4) == (1, 5, 9, 10)
    assert cage.sample_frames(3, 3, 8) == (3,)


def test_character_cage_rejects_inverted_range(blender_env):
    cage = blender_env.collider_proxy.character_collision_cage
    with pytest.raises(cage.CharacterCageError, match="must not precede"):
        cage.sample_frames(10, 1, 1)


def test_character_cage_properties_are_registered(blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Body", type="MESH")
    settings = obj.cloth_next
    assert settings.collider_proxy_type == "SIMPLE"
    assert settings.collider_cage_margin == pytest.approx(0.003)
    assert settings.collider_cage_joint_overlap == pytest.approx(0.01)
    assert settings.collider_cage_sample_step == 1
    assert settings.collider_cage_weight_threshold == pytest.approx(0.2)
    assert settings.collider_cage_min_vertices == 24
    env.registration.unregister()
