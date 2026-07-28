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
            records.append(SolverInputTriangle(owner, vertices))
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
        *, tolerance: float = 1.0e-8) -> tuple[int, int] | None:
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
            and _matches(raw_triangle, item.vertices)), None)
        if found is not None and found not in matched:
            matched.append(found)
    if not matched:
        return None
    return (matched[0], matched[1] if len(matched) > 1 else -1)


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
