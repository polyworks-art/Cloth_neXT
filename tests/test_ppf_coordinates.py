# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender <-> PPF coordinate transform (verified against upstream)."""

from __future__ import annotations

import math

import pytest

from cloth_next.ppf import coordinates as co


def test_origin_and_unit_axes():
    assert co.blender_position_to_ppf((0, 0, 0)) == (0.0, 0.0, 0.0)
    assert co.blender_position_to_ppf((1, 0, 0)) == (1.0, 0.0, 0.0)
    assert co.blender_position_to_ppf((0, 1, 0)) == (0.0, 0.0, -1.0)
    assert co.blender_position_to_ppf((0, 0, 1)) == (0.0, 1.0, 0.0)
    assert co.ppf_position_to_blender((0, 0, -1)) == (0.0, 1.0, 0.0)
    assert co.ppf_position_to_blender((0, 1, 0)) == (0.0, 0.0, 1.0)


def test_gravity_and_arbitrary_vectors():
    assert co.blender_vector_to_ppf((0.0, 0.0, -9.81)) == (0.0, -9.81, -0.0)
    assert co.blender_vector_to_ppf((1.5, -2.5, 3.5)) == (1.5, 3.5, 2.5)
    assert co.ppf_vector_to_blender((1.5, 3.5, 2.5)) == (1.5, -2.5, 3.5)


@pytest.mark.parametrize("vector", [
    (0.0, 0.0, 0.0), (1.0, 2.0, 3.0), (-1.25, 4.5, -9.81),
    (1e-9, -1e9, 123.456),
])
def test_roundtrip_within_float_tolerance(vector):
    forward = co.blender_position_to_ppf(vector)
    back = co.ppf_position_to_blender(forward)
    assert all(math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-15)
               for a, b in zip(vector, back))
    forward_direction = co.blender_vector_to_ppf(vector)
    assert co.ppf_vector_to_blender(forward_direction) == back


def test_solver_world_matrix_matches_position_transform():
    world = ((1, 0, 0, 0.5), (0, 1, 0, -1.5), (0, 0, 1, 2.0), (0, 0, 0, 1))
    solver = co.solver_world_matrix(world)
    for local in ((0, 0, 0), (1, 2, 3), (-4, 5, -6)):
        blender_world_point = (local[0] + 0.5, local[1] - 1.5, local[2] + 2.0)
        expected = co.blender_position_to_ppf(blender_world_point)
        actual = co.transform_point(solver, local)
        assert all(math.isclose(a, b, abs_tol=1e-12)
                   for a, b in zip(expected, actual))


def test_solver_world_to_object_local_roundtrip():
    world = ((0.0, -2.0, 0.0, 1.0), (2.0, 0.0, 0.0, -3.0),
             (0.0, 0.0, 2.0, 0.5), (0.0, 0.0, 0.0, 1.0))
    forward = co.solver_world_matrix(world)
    inverse = co.solver_world_to_object_local(world)
    for local in ((0, 0, 0), (1, 1, 1), (-2.5, 0.25, 7.0)):
        solver_point = co.transform_point(forward, local)
        back = co.transform_point(inverse, solver_point)
        assert all(math.isclose(a, b, abs_tol=1e-9)
                   for a, b in zip(local, back))


def test_matrix_invert_rejects_singular():
    singular = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 0, 0), (0, 0, 0, 1))
    with pytest.raises(ValueError):
        co.matrix_invert(singular)
    assert not co.matrix_is_finite_and_invertible(singular)
    nan_matrix = ((float("nan"),) * 4,) * 4
    assert not co.matrix_is_finite_and_invertible(nan_matrix)
    identity = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
    assert co.matrix_is_finite_and_invertible(identity)
