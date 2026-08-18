# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure, immutable mapping of solver intersection indices to scene elements."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class TriangleOwner:
    combined_triangle_index: int
    object_uuid: str
    object_name: str
    role: str
    local_triangle_index: int
    source_polygon_index: int | None
    generated_proxy: bool = False
    internal: bool = False


@dataclass(frozen=True, slots=True)
class SolverInputTriangle:
    owner: TriangleOwner
    vertices: tuple[tuple[float, float, float], ...]
    input_vertices: tuple[tuple[float, float, float], ...] = ()
    vertex_indices: tuple[int, int, int] = ()


@dataclass(frozen=True, slots=True)
class IntersectionElement:
    kind: str
    object_uuid: str
    object_name: str
    role: str
    combined_triangle_index: int
    local_triangle_index: int
    source_polygon_index: int | None
    vertices: tuple[tuple[float, float, float], ...]
    generated_proxy: bool = False


@dataclass(frozen=True, slots=True)
class IntersectionViolation:
    classification: str
    detection_method: str
    elements: tuple[IntersectionElement, ...]
    combined_pair: tuple[int, int]
    total_count: int


@dataclass(frozen=True, slots=True)
class SolverInputSnapshot:
    bake_start_frame: int
    triangles: tuple[SolverInputTriangle, ...]

    @property
    def owners(self) -> tuple[TriangleOwner, ...]:
        return tuple(item.owner for item in self.triangles)


_DEFORMABLE_ROLES = frozenset({"CLOTH", "SOFT_BODY", "RIGID_BODY", "ROD"})


def transform_point(matrix, point) -> tuple[float, float, float]:
    x, y, z = map(float, point)
    result = tuple(
        float(row[0]) * x + float(row[1]) * y + float(row[2]) * z
        + float(row[3])
        for row in matrix[:3])
    return result


def build_solver_input_snapshot(
        objects: Iterable[tuple[object, str, Sequence[int] | None, bool]],
        *, bake_start_frame: int) -> SolverInputSnapshot:
    """Build the one authoritative combined-triangle ownership table.

    ``objects`` must be in the same order used by scene encoding. Each entry is
    ``(SceneObject, role, source_polygon_indices, generated_proxy)``.
    """
    records = []
    combined_index = 0
    for scene_object, role, source_polygons, generated_proxy in objects:
        internal = str(getattr(scene_object, "uuid", "")).startswith(
            "cloth-next-internal-static")
        polygons = tuple(source_polygons or ())
        for local_index, triangle in enumerate(scene_object.triangles):
            input_vertices = tuple(
                tuple(map(float, scene_object.vertices_local[index]))
                for index in triangle)
            vertices = tuple(transform_point(
                scene_object.transform,
                scene_object.vertices_local[index]) for index in triangle)
            owner = TriangleOwner(
                combined_triangle_index=combined_index,
                object_uuid=str(scene_object.uuid),
                object_name=str(scene_object.name),
                role=str(role),
                local_triangle_index=local_index,
                source_polygon_index=(
                    int(polygons[local_index])
                    if local_index < len(polygons) else None),
                generated_proxy=bool(generated_proxy),
                internal=internal)
            records.append(SolverInputTriangle(
                owner, vertices, input_vertices=input_vertices,
                vertex_indices=tuple(map(int, triangle))))
            combined_index += 1
    return SolverInputSnapshot(int(bake_start_frame), tuple(records))


def _classification(first: TriangleOwner, second: TriangleOwner,
                    *, is_rod=False) -> str:
    if is_rod or first.role == "ROD" or second.role == "ROD":
        return "ROD_TRIANGLE_INTERSECTION"
    first_collider = first.role == "COLLIDER"
    second_collider = second.role == "COLLIDER"
    if first_collider != second_collider:
        return "INITIAL_COLLIDER_PENETRATION"
    if (first.role in _DEFORMABLE_ROLES
            and second.role in _DEFORMABLE_ROLES
            and first.object_uuid != second.object_uuid):
        return "DEFORMABLE_INTERSECTION"
    return "SELF_INTERSECTION"


def convert_violation(raw: Mapping, snapshot: SolverInputSnapshot, *,
                      total_count: int = 1) -> IntersectionViolation | None:
    """Enrich a new or legacy solver violation without guessing geometry."""
    pair_value = raw.get("combined_pair", raw.get("pair"))
    if (not isinstance(pair_value, (list, tuple))
            or len(pair_value) != 2):
        pair_value = _match_legacy_triangle_pair(raw.get("tris"), snapshot)
    if (not isinstance(pair_value, (list, tuple))
            or len(pair_value) != 2):
        return None
    try:
        pair = (int(pair_value[0]), int(pair_value[1]))
    except (TypeError, ValueError):
        return None
    by_index = {
        item.owner.combined_triangle_index: item
        for item in snapshot.triangles}
    # If either side is internal sentinel geometry, suppress the complete
    # diagnostic. Presenting only the artist-owned half recreates the very
    # one-sided preview bug this mapping is designed to prevent.
    if any(index >= 0 and (
            index not in by_index or by_index[index].owner.internal)
            for index in pair):
        return None
    elements = []
    for index in pair:
        if index < 0:
            continue
        item = by_index.get(index)
        if item is None or item.owner.internal:
            continue
        owner = item.owner
        elements.append(IntersectionElement(
            kind="TRIANGLE", object_uuid=owner.object_uuid,
            object_name=owner.object_name, role=owner.role,
            combined_triangle_index=index,
            local_triangle_index=owner.local_triangle_index,
            source_polygon_index=owner.source_polygon_index,
            vertices=item.vertices, generated_proxy=owner.generated_proxy))
    if not elements:
        return None
    method = str(raw.get("detection_method", "STRICT_CROSSING"))
    primary = _classification(
        by_index[elements[0].combined_triangle_index].owner,
        by_index[elements[-1].combined_triangle_index].owner,
        is_rod=bool(raw.get("is_rod", pair[0] < 0)))
    return IntersectionViolation(
        classification=primary, detection_method=method,
        elements=tuple(elements), combined_pair=pair,
        total_count=max(1, int(total_count)))


def _match_legacy_triangle_pair(
        raw_triangles, snapshot: SolverInputSnapshot,
        *, tolerance: float = 1.0e-6) -> tuple[int, int] | None:
    """Map solver-provided legacy triangle geometry to the input snapshot.

    Older solver builds expose only the detected triangle positions.  Matching
    those exact positions is lossless attribution, not a second intersection
    test.  A one-sided solver preview uses ``-1`` for the unavailable partner
    so the confirmed offending face can still be shown to the artist.
    """
    if not isinstance(raw_triangles, (list, tuple)) or not raw_triangles:
        return None

    def _matches(left, right) -> bool:
        if (not isinstance(left, (list, tuple)) or len(left) != 3):
            return False
        try:
            remaining = [tuple(map(float, point)) for point in right]
            candidate = [tuple(map(float, point)) for point in left]
        except (TypeError, ValueError):
            return False
        for point in candidate:
            match = next((
                index for index, expected in enumerate(remaining)
                if all(abs(point[axis] - expected[axis]) <= tolerance
                       for axis in range(3))), None)
            if match is None:
                return False
            remaining.pop(match)
        return True

    matched = []
    for raw_triangle in raw_triangles[:2]:
        found = next((
            item.owner.combined_triangle_index
            for item in snapshot.triangles
            if not item.owner.internal
            and (_matches(raw_triangle, item.vertices)
                 or _matches(raw_triangle, item.input_vertices))), None)
        if found is not None and found not in matched:
            matched.append(found)
    if not matched:
        return None
    return (matched[0], matched[1] if len(matched) > 1 else -1)


def triangles_strictly_cross(first, second, *,
                             tolerance: float = 1.0e-9) -> bool:
    """Return whether either triangle has an edge crossing the other.

    This is used only to locate faces after the solver has already confirmed
    an intersection but its legacy binding omitted the pair indices.
    """
    def _sub(a, b):
        return tuple(float(a[i]) - float(b[i]) for i in range(3))

    def _cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def _dot(a, b):
        return sum(a[i] * b[i] for i in range(3))

    def _edge_hits_triangle(start, end, triangle):
        direction = _sub(end, start)
        edge1 = _sub(triangle[1], triangle[0])
        edge2 = _sub(triangle[2], triangle[0])
        pvec = _cross(direction, edge2)
        determinant = _dot(edge1, pvec)
        if abs(determinant) <= tolerance:
            return False
        inverse = 1.0 / determinant
        tvec = _sub(start, triangle[0])
        u = _dot(tvec, pvec) * inverse
        if u <= tolerance or u >= 1.0 - tolerance:
            return False
        qvec = _cross(tvec, edge1)
        v = _dot(direction, qvec) * inverse
        if v <= tolerance or u + v >= 1.0 - tolerance:
            return False
        distance = _dot(edge2, qvec) * inverse
        return tolerance < distance < 1.0 - tolerance

    for source, target in ((first, second), (second, first)):
        for index in range(3):
            if _edge_hits_triangle(
                    source[index], source[(index + 1) % 3], target):
                return True
    return False


def triangles_coplanar_overlap(first, second, *,
                               tolerance: float = 1.0e-9) -> bool:
    """Detect proper coplanar overlap without flagging shared boundaries."""
    def _sub(a, b):
        return tuple(float(a[i]) - float(b[i]) for i in range(3))

    def _cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    def _dot(a, b):
        return sum(a[i] * b[i] for i in range(3))

    normal = _cross(_sub(first[1], first[0]), _sub(first[2], first[0]))
    magnitude = sum(value * value for value in normal) ** 0.5
    if magnitude <= tolerance:
        return False
    if any(abs(_dot(normal, _sub(point, first[0]))) >
           tolerance * magnitude for point in second):
        return False
    drop = max(range(3), key=lambda axis: abs(normal[axis]))

    def _project(point):
        return tuple(float(point[axis]) for axis in range(3) if axis != drop)

    left = tuple(_project(point) for point in first)
    right = tuple(_project(point) for point in second)

    def _orient(a, b, c):
        return ((b[0] - a[0]) * (c[1] - a[1])
                - (b[1] - a[1]) * (c[0] - a[0]))

    def _proper_edges(a, b, c, d):
        ab_c, ab_d = _orient(a, b, c), _orient(a, b, d)
        cd_a, cd_b = _orient(c, d, a), _orient(c, d, b)
        return (ab_c * ab_d < -tolerance
                and cd_a * cd_b < -tolerance)

    def _strictly_inside(point, triangle):
        values = tuple(
            _orient(triangle[index], triangle[(index + 1) % 3], point)
            for index in range(3))
        return (all(value > tolerance for value in values)
                or all(value < -tolerance for value in values))

    for first_index in range(3):
        for second_index in range(3):
            if _proper_edges(
                    left[first_index], left[(first_index + 1) % 3],
                    right[second_index], right[(second_index + 1) % 3]):
                return True
    if any(_strictly_inside(point, right) for point in left):
        return True
    if any(_strictly_inside(point, left) for point in right):
        return True
    # Identical duplicate faces have no proper crossings or interior vertex.
    canonical_left = sorted(
        tuple(round(value / tolerance) for value in point) for point in left)
    canonical_right = sorted(
        tuple(round(value / tolerance) for value in point) for point in right)
    return canonical_left == canonical_right


def artist_message(violation: IntersectionViolation) -> tuple[str, str]:
    elements = violation.elements
    first = elements[0]
    second = elements[1] if len(elements) > 1 else elements[0]
    if violation.classification == "INITIAL_COLLIDER_PENETRATION":
        deformable = first if first.role != "COLLIDER" else second
        collider = second if first.role != "COLLIDER" else first
        summary = (
            f"{deformable.object_name} intersects {collider.object_name} "
            "at the Bake start frame.")
        action = (
            "Move the deformable away from the Collider, adjust its proxy, "
            "or inspect the highlighted faces before baking again.")
        if collider.generated_proxy:
            action += " Inspect or regenerate the Collider proxy."
        return summary, action
    if violation.classification == "DEFORMABLE_INTERSECTION":
        return (
            f"{first.object_name} intersects {second.object_name} during "
            "the initial solver pose.",
            "Separate the highlighted deformable surfaces before baking again.")
    if violation.classification == "ROD_TRIANGLE_INTERSECTION":
        return (
            f"{first.object_name} intersects {second.object_name} at the "
            "Bake start frame.",
            "Move the highlighted cable or surface apart before baking again.")
    if violation.detection_method == "COPLANAR_OVERLAP":
        return (
            f"{first.object_name} contains overlapping faces in the initial "
            "solver pose.",
            "Separate the overlapping surfaces or remove duplicated faces.")
    return (
        f"{first.object_name} contains a self-intersection between two "
        "non-adjacent faces.",
        "Inspect the highlighted faces for folded, duplicated, or crossing "
        "geometry.")


def triangle_metrics(vertices) -> tuple[float, float]:
    edges = []
    for triangle in vertices:
        for index in range(3):
            a, b = triangle[index], triangle[(index + 1) % 3]
            edges.append(sqrt(sum((a[axis] - b[axis]) ** 2
                                  for axis in range(3))))
    return ((sum(edges) / len(edges) if edges else 0.0),
            (min(edges) if edges else 0.0))
