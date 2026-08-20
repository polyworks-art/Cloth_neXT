# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure conservative displacement planning for confirmed intersections."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite, sqrt

from . import intersection_diagnostics

SUPPORTED_CLASSIFICATIONS = frozenset({"SELF_INTERSECTION"})
MINIMUM_SEPARATION_EPSILON = 1.0e-7
SEPARATION_SAFETY_MARGIN = 1.15
MAX_CORRECTION_EDGE_FRACTION = 0.08
MAX_REPAIR_PASSES = 3
ZERO_AREA_CROSS_EPSILON = 1.0e-12
MINIMUM_DEGENERATE_EDGE_FRACTION = 1.0e-5
MAX_DEGENERATE_EDGE_FRACTION = 0.02
DEGENERATE_WELD_RELATIVE_TOLERANCE = 1.0e-9
DEGENERATE_WELD_ABSOLUTE_TOLERANCE = 1.0e-12


@dataclass(frozen=True, slots=True)
class DegenerateRepairPlan:
    displacements: dict
    repaired_faces: int
    skipped_faces: int


@dataclass(frozen=True, slots=True)
class IntersectionRepairPlan:
    displacements: dict
    repaired_pairs: int
    skipped_pairs: int
    repaired_pair_records: tuple = ()


@dataclass(frozen=True, slots=True)
class DegenerateWeldPlan:
    vertex_groups: tuple
    faces: tuple


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


def _planned_triangle(keys, triangle, planned):
    return tuple(_add(point, planned.get(key, (0.0, 0.0, 0.0)))
                 for key, point in zip(keys, triangle))


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


def _triangles_overlap(first, second):
    return (intersection_diagnostics.triangles_strictly_cross(first, second)
            or intersection_diagnostics.triangles_coplanar_overlap(
                first, second))


def _pair_vertex_keys(pair):
    return frozenset((*pair[0], *pair[2]))


def _connected_pair_clusters(pairs):
    remaining = list(pairs)
    clusters = []
    while remaining:
        cluster = [remaining.pop(0)]
        keys = set(_pair_vertex_keys(cluster[0]))
        changed = True
        while changed:
            changed = False
            for pair in tuple(remaining):
                pair_keys = _pair_vertex_keys(pair)
                if keys.intersection(pair_keys):
                    remaining.remove(pair)
                    cluster.append(pair)
                    keys.update(pair_keys)
                    changed = True
        clusters.append(tuple(cluster))
    return tuple(clusters)


def _plan_pair_cluster(pairs, desired):
    contributions = defaultdict(list)
    limits = defaultdict(list)
    for first_keys, first, second_keys, second in pairs:
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
            limits[key].append(correction * 0.5)
        for key in second_keys:
            contributions[key].append(_mul(half, -1.0))
            limits[key].append(correction * 0.5)
    return _average_contributions(contributions, limits)


def _bounds(points):
    return tuple((min(point[axis] for point in points),
                  max(point[axis] for point in points)) for axis in range(3))


def _bounds_overlap(first, second, tolerance=1.0e-9):
    return all(first[axis][0] <= second[axis][1] + tolerance
               and second[axis][0] <= first[axis][1] + tolerance
               for axis in range(3))


def _plan_is_safe(pairs, planned, validation_triangles):
    if any(_triangles_overlap(
            _planned_triangle(first_keys, first, planned),
            _planned_triangle(second_keys, second, planned))
           for first_keys, first, second_keys, second in pairs):
        return False
    if not validation_triangles:
        return True

    moved_keys = frozenset(planned)
    affected = tuple(
        record for record in validation_triangles
        if moved_keys.intersection(record[0]))
    if not affected:
        return False
    swept_points = tuple(
        point
        for keys, triangle in affected
        for point in (*triangle, *_planned_triangle(keys, triangle, planned)))
    swept_bounds = _bounds(swept_points)
    relevant = tuple(
        record for record in validation_triangles
        if record in affected or _bounds_overlap(_bounds(record[1]), swept_bounds))
    affected_ids = {id(record) for record in affected}
    before = set()
    after = set()
    for left_index, left in enumerate(relevant):
        for right in relevant[left_index + 1:]:
            if not ({id(left), id(right)} & affected_ids):
                continue
            if set(left[0]).intersection(right[0]):
                continue
            identity = tuple(sorted((repr(left[0]), repr(right[0]))))
            if _triangles_overlap(left[1], right[1]):
                before.add(identity)
            if _triangles_overlap(
                    _planned_triangle(*left, planned),
                    _planned_triangle(*right, planned)):
                after.add(identity)
    return not (after - before)


def validate_intersection_plan(pairs, planned, validation_triangles=()):
    """Validate resolved pairs and reject newly introduced local crossings."""
    return _plan_is_safe(
        tuple(pairs), dict(planned), tuple(validation_triangles))


def plan_intersection_repairs(confirmed_pairs, *, desired_separation,
                              validation_triangles=()):
    """Plan each independent intersection cluster and retain only safe ones."""
    pairs = tuple(confirmed_pairs)
    validation = tuple(validation_triangles)
    desired = float(desired_separation)
    if not isfinite(desired) or desired < 0.0:
        raise ValueError("desired_separation must be a finite non-negative value")
    planned = {}
    accepted = []
    for cluster in _connected_pair_clusters(pairs):
        candidate = _plan_pair_cluster(cluster, desired)
        combined = (dict(candidate) if not planned
                    else combine_displacement_plans(planned, candidate))
        accepted_pairs = tuple((*accepted, *cluster))
        if (candidate and _plan_is_safe(
                accepted_pairs, combined, validation)):
            planned = combined
            accepted.extend(cluster)
    return IntersectionRepairPlan(
        planned, len(accepted), len(pairs) - len(accepted), tuple(accepted))


def plan_displacements(confirmed_pairs, *, desired_separation,
                       validation_triangles=()):
    """Compatibility wrapper returning only safe planned displacements."""
    return plan_intersection_repairs(
        confirmed_pairs, desired_separation=desired_separation,
        validation_triangles=validation_triangles).displacements


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


def _face_after_plan(face, planned):
    object_uuid = str(getattr(face, "object_uuid", ""))
    return tuple(
        _add(point, planned.get((object_uuid, int(index)), (0.0, 0.0, 0.0)))
        for index, point in zip(face.vertex_indices, face.vertices))


def _face_is_repaired(face, planned):
    vertices = _face_after_plan(face, planned)
    return (_length(_cross(_sub(vertices[1], vertices[0]),
                           _sub(vertices[2], vertices[0])))
            > ZERO_AREA_CROSS_EPSILON)


def _coincident_distinct_vertices(indices, vertices):
    if len(indices) != 3 or len(set(indices)) != 3 or len(vertices) != 3:
        return False
    edge_scale = max(
        _length(_sub(vertices[left], vertices[right]))
        for left in range(3) for right in range(left + 1, 3))
    tolerance = max(
        DEGENERATE_WELD_ABSOLUTE_TOLERANCE,
        edge_scale * DEGENERATE_WELD_RELATIVE_TOLERANCE)
    return any(
        _length(_sub(vertices[left], vertices[right])) <= tolerance
        for left in range(3) for right in range(left + 1, 3))


def evaluate_degenerate_repairs(faces, planned):
    """Count only faces that are non-degenerate after the final plan."""
    faces = tuple(faces)
    repaired = sum(_face_is_repaired(face, planned) for face in faces)
    return repaired, len(faces) - repaired


def plan_degenerate_welds(faces, planned):
    """Identify unresolved faces caused by coincident distinct vertex IDs.

    This is ID-only planning: it never searches surrounding mesh vertices.
    Blender-side validation must still prove attribute and topology safety.
    """
    candidates = []
    candidate_faces = []
    for face in faces:
        if _face_is_repaired(face, planned):
            continue
        indices = tuple(getattr(face, "vertex_indices", ()))
        vertices = tuple(getattr(face, "vertices", ()))
        object_uuid = str(getattr(face, "object_uuid", ""))
        if (not object_uuid or len(indices) != 3 or len(set(indices)) != 3
                or len(vertices) != 3):
            continue
        edge_scale = max(
            _length(_sub(vertices[left], vertices[right]))
            for left in range(3) for right in range(left + 1, 3))
        tolerance = max(DEGENERATE_WELD_ABSOLUTE_TOLERANCE,
                        edge_scale * DEGENERATE_WELD_RELATIVE_TOLERANCE)
        duplicate_pairs = [
            ((object_uuid, int(indices[left])),
             (object_uuid, int(indices[right])))
            for left in range(3) for right in range(left + 1, 3)
            if _length(_sub(vertices[left], vertices[right])) <= tolerance]
        if not duplicate_pairs:
            continue
        candidates.extend(duplicate_pairs)
        candidate_faces.append(face)

    groups = []
    for pair in candidates:
        overlapping = [group for group in groups
                       if set(group).intersection(pair)]
        merged = set(pair)
        for group in overlapping:
            groups.remove(group)
            merged.update(group)
        groups.append(tuple(sorted(merged, key=repr)))
    return DegenerateWeldPlan(
        tuple(sorted(groups, key=repr)), tuple(candidate_faces))


def plan_degenerate_displacements(faces, *, desired_separation):
    """Plan bounded local repairs independent of cloth collision settings."""
    faces = tuple(faces)
    desired = float(desired_separation)
    if not isfinite(desired) or desired < 0.0:
        raise ValueError("desired_separation must be a finite non-negative value")
    contributions = defaultdict(list)
    limits = defaultdict(list)
    candidates = []
    for face in faces:
        indices = tuple(getattr(face, "vertex_indices", ()))
        vertices = tuple(getattr(face, "vertices", ()))
        object_uuid = str(getattr(face, "object_uuid", ""))
        if (len(indices) != 3 or len(set(indices)) != 3
                or len(vertices) != 3 or not object_uuid):
            continue
        try:
            if not all(isfinite(float(component))
                       for vertex in vertices for component in vertex):
                continue
            cross_length = _length(_cross(
                _sub(vertices[1], vertices[0]),
                _sub(vertices[2], vertices[0])))
        except (IndexError, TypeError, ValueError):
            continue
        if cross_length > ZERO_AREA_CROSS_EPSILON:
            continue
        # Pulling apart coincident but distinct source IDs can open seams and
        # create crossings. Route them to the explicit-ID weld safety path.
        if _coincident_distinct_vertices(indices, vertices):
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
            continue
        limit = edge_length * MAX_DEGENERATE_EDGE_FRACTION
        correction = max(
            edge_length * MINIMUM_DEGENERATE_EDGE_FRACTION,
            ZERO_AREA_CROSS_EPSILON * SEPARATION_SAFETY_MARGIN / edge_length,
            MINIMUM_SEPARATION_EPSILON)
        if correction <= 0.0 or correction > limit:
            continue
        key = (object_uuid, int(indices[opposite]))
        delta = _mul(direction, correction)
        contributions[key].append(delta)
        limits[key].append(correction)
        candidates.append((face, key, delta, correction))

    planned = _average_contributions(contributions, limits)
    # Averaging shared-vertex contributions can cancel a previously valid
    # single-face correction. Remove failed contributions and recompute until
    # the retained proposal set is self-consistent.
    active = list(candidates)
    while active:
        repaired = {id(face) for face, _key, _delta, _limit in active
                    if _face_is_repaired(face, planned)}
        failed = [entry for entry in active if id(entry[0]) not in repaired]
        if not failed:
            break
        active = [entry for entry in active if id(entry[0]) in repaired]
        active_contributions = defaultdict(list)
        active_limits = defaultdict(list)
        for _face, key, delta, limit in active:
            active_contributions[key].append(delta)
            active_limits[key].append(limit)
        planned = _average_contributions(
            active_contributions, active_limits)
    repaired, skipped = evaluate_degenerate_repairs(faces, planned)
    return DegenerateRepairPlan(planned, repaired, skipped)


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
