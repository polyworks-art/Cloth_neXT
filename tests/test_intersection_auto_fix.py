# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for conservative intersection repair planning."""

import math
from types import SimpleNamespace

import pytest

from cloth_next.ppf.schema.data import zero_area_triangles
from cloth_next.intersection_auto_fix import (
    MAX_CORRECTION_EDGE_FRACTION,
    MAX_DEGENERATE_EDGE_FRACTION,
    ZERO_AREA_CROSS_EPSILON,
    combine_displacement_plans,
    is_supported_classification,
    plan_degenerate_displacements,
    plan_displacements,
    separation_direction,
)


FIRST = ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0))
SECOND = ((-1.0, -1.0, 0.01), (0.0, 1.0, 0.01), (1.0, -1.0, 0.01))


def _dot(first, second):
    return sum(a * b for a, b in zip(first, second))


def _length(value):
    return math.sqrt(_dot(value, value))


def _pair(first_keys=(0, 1, 2), second_keys=(3, 4, 5)):
    return first_keys, FIRST, second_keys, SECOND


def test_supported_classification_is_conservative():
    assert is_supported_classification("SELF_INTERSECTION")
    assert is_supported_classification(" self_intersection ")
    assert not is_supported_classification("COLLIDER_INTERSECTION")
    assert not is_supported_classification("DEFORMABLE_INTERSECTION")
    assert not is_supported_classification(None)


def test_direction_moves_pair_farther_apart():
    crossing = ((0.0, -0.5, -1.0), (0.0, 0.5, 1.0), (0.0, 0.5, -1.0))
    direction = separation_direction(FIRST, crossing)
    assert direction is not None
    planned = plan_displacements(
        [((0, 1, 2), FIRST, (3, 4, 5), crossing)],
        desired_separation=0.02,
    )
    first_motion = tuple(sum(planned[key][axis] for key in (0, 1, 2)) / 3.0
                         for axis in range(3))
    second_motion = tuple(sum(planned[key][axis] for key in (3, 4, 5)) / 3.0
                          for axis in range(3))
    relative_motion = tuple(a - b for a, b in zip(first_motion, second_motion))
    assert _dot(relative_motion, direction) > 0.0


def test_shared_vertex_contributions_cancel_instead_of_multiplying():
    planned = plan_displacements(
        [_pair((0, 1, 2), (0, 3, 4))], desired_separation=0.02
    )
    assert planned[0] == pytest.approx((0.0, 0.0, 0.0))
    assert planned[1] == pytest.approx(planned[2])
    assert planned[3] == pytest.approx(planned[4])
    assert planned[1] == pytest.approx(tuple(-v for v in planned[3]))


def test_repeated_pairs_are_averaged_not_summed():
    once = plan_displacements([_pair()], desired_separation=0.02)
    repeated = plan_displacements([_pair()] * 18, desired_separation=0.02)
    assert repeated.keys() == once.keys()
    for key in once:
        assert repeated[key] == pytest.approx(once[key])


def test_correction_is_clamped_to_local_edge_scale():
    planned = plan_displacements([_pair()], desired_separation=1000.0)
    edges = (2.0, math.sqrt(5.0), math.sqrt(5.0)) * 2
    expected_half_limit = (sum(edges) / len(edges)) * MAX_CORRECTION_EDGE_FRACTION / 2.0
    assert max(map(_length, planned.values())) == pytest.approx(expected_half_limit)


def test_planning_is_deterministic_for_identical_input():
    pairs = [_pair(), _pair((2, 6, 7), (8, 9, 10))]
    assert plan_displacements(pairs, desired_separation=0.02) == plan_displacements(
        pairs, desired_separation=0.02
    )


@pytest.mark.parametrize("invalid", [-1.0, math.inf, math.nan])
def test_invalid_desired_separation_is_rejected(invalid):
    with pytest.raises(ValueError):
        plan_displacements([_pair()], desired_separation=invalid)


def _face(indices=(0, 1, 2), vertices=((0.0, 0.0, 0.0),
                                      (1.0, 0.0, 0.0),
                                      (2.0, 0.0, 0.0)), uuid="cloth"):
    return SimpleNamespace(
        object_uuid=uuid, vertex_indices=indices, vertices=vertices)


def _triangle_area(vertices):
    first = tuple(vertices[1][axis] - vertices[0][axis] for axis in range(3))
    second = tuple(vertices[2][axis] - vertices[0][axis] for axis in range(3))
    cross = (first[1] * second[2] - first[2] * second[1],
             first[2] * second[0] - first[0] * second[2],
             first[0] * second[1] - first[1] * second[0])
    return _length(cross) * 0.5


def test_zero_area_face_gets_positive_bounded_area():
    face = _face()
    result = plan_degenerate_displacements(
        (face,), desired_separation=0.01)
    repaired = [list(point) for point in face.vertices]
    for (_uuid, index), delta in result.displacements.items():
        repaired[index] = [repaired[index][axis] + delta[axis]
                           for axis in range(3)]

    assert result.repaired_faces == 1
    assert result.skipped_faces == 0
    assert _triangle_area(repaired) > ZERO_AREA_CROSS_EPSILON * 0.5
    assert zero_area_triangles(repaired, ((0, 1, 2),)) == []
    assert max(map(_length, result.displacements.values())) <= (
        2.0 * MAX_DEGENERATE_EDGE_FRACTION)


def test_duplicate_position_is_repaired_without_topology_change():
    face = _face(vertices=((0.0, 0.0, 0.0),
                          (1.0, 0.0, 0.0),
                          (1.0, 0.0, 0.0)))
    result = plan_degenerate_displacements(
        (face,), desired_separation=0.01)

    assert result.repaired_faces == 1
    assert result.skipped_faces == 0
    assert len(result.displacements) == 1


def test_repeated_vertex_index_is_skipped():
    result = plan_degenerate_displacements(
        (_face(indices=(0, 0, 1)),), desired_separation=0.01)

    assert result.displacements == {}
    assert result.repaired_faces == 0
    assert result.skipped_faces == 1


def test_multiple_degenerate_faces_are_planned_deterministically():
    faces = (_face(), _face(indices=(3, 4, 5), uuid="other"))

    first = plan_degenerate_displacements(faces, desired_separation=0.01)
    second = plan_degenerate_displacements(faces, desired_separation=0.01)

    assert first == second
    assert first.repaired_faces == 2
    assert first.skipped_faces == 0
    assert len(first.displacements) == 2


def test_combined_shared_vertex_repairs_are_averaged_and_bounded():
    intersection = {("cloth", 1): (0.0, 0.0, 0.04)}
    degenerate = {("cloth", 1): (0.0, 0.02, 0.0)}

    combined = combine_displacement_plans(intersection, degenerate)

    assert combined[("cloth", 1)] == pytest.approx((0.0, 0.01, 0.02))
    assert _length(combined[("cloth", 1)]) < 0.04
