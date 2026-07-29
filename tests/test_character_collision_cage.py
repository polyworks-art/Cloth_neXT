# SPDX-License-Identifier: GPL-3.0-or-later

import numpy as np
import pytest


def test_character_cage_frame_sampling_includes_end(blender_env):
    cage = blender_env.collider_proxy.character_collision_cage
    assert cage.sample_frames(1, 10, 4) == (1, 5, 9, 10)
    assert cage.sample_frames(3, 3, 8) == (3,)


def test_character_cage_rejects_inverted_range(blender_env):
    cage = blender_env.collider_proxy.character_collision_cage
    with pytest.raises(cage.CharacterCageError, match="must not precede"):
        cage.sample_frames(10, 1, 1)


def test_character_cage_halfspaces_are_conservative(blender_env):
    cage = blender_env.collider_proxy.character_collision_cage
    directions = cage._support_directions()
    points = np.asarray([
        (-1.0, -2.0, -0.5), (-1.0, -2.0, 0.5),
        (-1.0, 2.0, -0.5), (-1.0, 2.0, 0.5),
        (1.0, -2.0, -0.5), (1.0, -2.0, 0.5),
        (1.0, 2.0, -0.5), (1.0, 2.0, 0.5),
    ], dtype=np.float64)
    support = np.max(points @ directions.T, axis=0)
    vertices = cage._halfspace_vertices(directions, support)

    assert len(vertices) >= 8
    assert np.all(directions @ vertices.T <= support[:, None] + 1e-6)
    assert np.all(points.min(axis=0) >= vertices.min(axis=0) - 1e-6)
    assert np.all(points.max(axis=0) <= vertices.max(axis=0) + 1e-6)


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
