# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure conservative displacement planning for confirmed intersections."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite, sqrt

SUPPORTED_CLASSIFICATIONS = frozenset({"SELF_INTERSECTION"})
MINIMUM_SEPARATION_EPSILON = 1.0e-7
SEPARATION_SAFETY_MARGIN = 1.15
MAX_CORRECTION_EDGE_FRACTION = 0.08
MAX_REPAIR_PASSES = 3
ZERO_AREA_CROSS_EPSILON = 1.0e-12
MINIMUM_DEGENERATE_EDGE_FRACTION = 1.0e-5
MAX_DEGENERATE_EDGE_FRACTION = 0.02


@dataclass(frozen=True, slots=True)
class DegenerateRepairPlan:
    displacements: dict
    repaired_faces: int
    skipped_faces: int


def is_supported_classification(classification):
    """Return whether a solver violation is safe for V1 automatic repair."""
    return (isinstance(classification, str)
            and classification.strip().upper() in SUPPORTED_CLASSIFICATIONS)


def _sub(a, b):
    return tuple(float(a[i]) - float(b[i]) for i in range(3))


def _add(a, b):
    return tuple(float(a[i]) + float(b[i]) for i in range(3))


def _mul(a, value):
    return tuple(float(item) * value for item in a)


def _dot(a, b):
    return sum(float(a[i]) * float(b[i]) for i in range(3))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _length(value):
    return sqrt(_dot(value, value))


def _unit(value):
    length = _length(value)
    return _mul(value, 1.0 / length) if length > 1.0e-14 else None


def _normal(triangle):
    return _unit(_cross(_sub(triangle[1], triangle[0]),
                        _sub(triangle[2], triangle[0])))


def _centroid(triangle):
    return tuple(sum(float(point[axis]) for point in triangle) / 3.0
                 for axis in range(3))


def _average_edge_length(triangles):
    edges = [_length(_sub(triangle[(i + 1) % 3], triangle[i]))
             for triangle in triangles for i in range(3)]
    return sum(edges) / len(edges) if edges else 0.0


def separation_direction(first, second):
    """Return a deterministic direction that sends the first side forward."""
    first_normal, second_normal = _normal(first), _normal(second)
    if first_normal is None or second_normal is None:
        return None
    # Treat opposite winding as the same unoriented surface normal.
    aligned_second = (_mul(second_normal, -1.0)
                      if _dot(first_normal, second_normal) < 0.0
                      else second_normal)
    direction = _unit(_add(first_normal, aligned_second)) or first_normal
    center_delta = _sub(_centroid(first), _centroid(second))
    if _dot(direction, center_delta) < 0.0:
        direction = _mul(direction, -1.0)
    elif abs(_dot(direction, center_delta)) <= 1.0e-14:
        # Winding-independent deterministic sign for exactly crossing centers.
        dominant = max(range(3), key=lambda axis: abs(direction[axis]))
        if direction[dominant] < 0.0:
            direction = _mul(direction, -1.0)
    return direction


def plan_displacements(confirmed_pairs, *, desired_separation):
    """Average contributions per vertex and clamp the final correction.

    Each pair is ``(first_keys, first_triangle, second_keys, second_triangle)``;
    keys may be any hashable source-vertex identifiers.
    """
    desired = float(desired_separation)
    if not isfinite(desired) or desired < 0.0:
        raise ValueError("desired_separation must be a finite non-negative value")

    contributions = defaultdict(list)
    contribution_limits = defaultdict(list)
    for first_keys, first, second_keys, second in confirmed_pairs:
        if len(first_keys) != 3 or len(second_keys) != 3:
            continue
        direction = separation_direction(first, second)
        edge = _average_edge_length((first, second))
        if direction is None or edge <= 0.0:
            continue
        correction = min(
            max(desired * SEPARATION_SAFETY_MARGIN,
                MINIMUM_SEPARATION_EPSILON),
            edge * MAX_CORRECTION_EDGE_FRACTION)
        half = _mul(direction, correction * 0.5)
        for key in first_keys:
            contributions[key].append(half)
            contribution_limits[key].append(correction * 0.5)
        for key in second_keys:
            contributions[key].append(_mul(half, -1.0))
            contribution_limits[key].append(correction * 0.5)
    planned = {}
    for key in sorted(contributions, key=repr):
        values = contributions[key]
        average = tuple(sum(value[axis] for value in values) / len(values)
                        for axis in range(3))
        # Averaging already bounds a vector when every contribution is bounded,
        # but retain an explicit final clamp as a safety invariant if the
        # contribution strategy changes later.
        limit = sum(contribution_limits[key]) / len(contribution_limits[key])
        average_length = _length(average)
        if average_length > limit and average_length > 0.0:
            average = _mul(average, limit / average_length)
        planned[key] = average
    return planned


def _deterministic_perpendicular(direction):
    """Return a stable unit vector perpendicular to a non-zero direction."""
    unit = _unit(direction)
    if unit is None:
        return None
    dominant = max(range(3), key=lambda index: (abs(unit[index]), -index))
    if unit[dominant] < 0.0:
        unit = _mul(unit, -1.0)
    axis = min(range(3), key=lambda index: (abs(unit[index]), index))
    basis = tuple(1.0 if index == axis else 0.0 for index in range(3))
    perpendicular = _unit(_cross(unit, basis))
    if perpendicular is None:
        return None
    dominant = max(
        range(3), key=lambda index: (abs(perpendicular[index]), -index))
    return (_mul(perpendicular, -1.0)
            if perpendicular[dominant] < 0.0 else perpendicular)


def _average_contributions(contributions, limits):
    planned = {}
    for key in sorted(contributions, key=repr):
        values = contributions[key]
        average = tuple(sum(value[axis] for value in values) / len(values)
                        for axis in range(3))
        limit = sum(limits[key]) / len(limits[key])
        length = _length(average)
        if length > limit and length > 0.0:
            average = _mul(average, limit / length)
        planned[key] = average
    return planned


def plan_degenerate_displacements(faces, *, desired_separation):
    """Plan bounded position-only repairs for attributed zero-area faces."""
    desired = float(desired_separation)
    if not isfinite(desired) or desired < 0.0:
        raise ValueError("desired_separation must be a finite non-negative value")
    contributions = defaultdict(list)
    limits = defaultdict(list)
    repaired = skipped = 0
    for face in faces:
        indices = tuple(getattr(face, "vertex_indices", ()))
        vertices = tuple(getattr(face, "vertices", ()))
        object_uuid = str(getattr(face, "object_uuid", ""))
        if (len(indices) != 3 or len(set(indices)) != 3
                or len(vertices) != 3 or not object_uuid):
            skipped += 1
            continue
        try:
            if not all(isfinite(float(component))
                       for vertex in vertices for component in vertex):
                skipped += 1
                continue
            cross_length = _length(_cross(
                _sub(vertices[1], vertices[0]),
                _sub(vertices[2], vertices[0])))
        except (IndexError, TypeError, ValueError):
            skipped += 1
            continue
        if cross_length > ZERO_AREA_CROSS_EPSILON:
            skipped += 1
            continue
        edges = (
            (_length(_sub(vertices[1], vertices[0])), 0, 1, 2),
            (_length(_sub(vertices[2], vertices[1])), 1, 2, 0),
            (_length(_sub(vertices[0], vertices[2])), 2, 0, 1),
        )
        edge_length, first, second, opposite = max(
            edges, key=lambda item: (item[0], -item[1], -item[2]))
        direction = _deterministic_perpendicular(
            _sub(vertices[second], vertices[first]))
        if direction is None or edge_length <= 0.0:
            skipped += 1
            continue
        limit = edge_length * MAX_DEGENERATE_EDGE_FRACTION
        correction = max(
            desired * SEPARATION_SAFETY_MARGIN,
            edge_length * MINIMUM_DEGENERATE_EDGE_FRACTION,
            ZERO_AREA_CROSS_EPSILON * SEPARATION_SAFETY_MARGIN / edge_length,
            MINIMUM_SEPARATION_EPSILON)
        if correction <= 0.0 or correction > limit:
            skipped += 1
            continue
        key = (object_uuid, int(indices[opposite]))
        delta = _mul(direction, correction)
        contributions[key].append(delta)
        limits[key].append(correction)
        repaired += 1
    return DegenerateRepairPlan(
        _average_contributions(contributions, limits), repaired, skipped)


def combine_displacement_plans(*plans):
    """Average independent repair plans so shared vertices remain bounded."""
    contributions = defaultdict(list)
    limits = defaultdict(list)
    for plan in plans:
        for key, value in plan.items():
            vector = tuple(map(float, value))
            length = _length(vector)
            contributions[key].append(vector)
            limits[key].append(length)
    combined = _average_contributions(contributions, limits)
    return {key: value for key, value in combined.items()
            if _length(value) > 1.0e-18}
