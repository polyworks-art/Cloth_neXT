# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic, bpy-independent self-intersection region construction."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from time import perf_counter
from typing import Iterable

from ..intersection_diagnostics import (triangles_coplanar_overlap,
                                        triangles_strictly_cross)
from .model import VeyraStep


Point = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class RegionTriangle:
    object_uuid: str
    triangle_index: int
    vertex_indices: tuple[int, int, int]
    vertices: tuple[Point, Point, Point]


@dataclass(frozen=True, slots=True)
class RegionSeed:
    first: int
    second: int

    @property
    def key(self) -> tuple[int, int]:
        return tuple(sorted((self.first, self.second)))


@dataclass(frozen=True, slots=True)
class IntersectionRegion:
    region_id: int
    object_uuid: str
    seeds: tuple[RegionSeed, ...]
    triangle_indices: tuple[int, ...]
    vertex_indices: tuple[int, ...]
    bounds: tuple[Point, Point]
    local_edge_scale: float
    side_a: tuple[int, ...] = ()
    side_b: tuple[int, ...] = ()
    ambiguous_sides: bool = False


@dataclass(frozen=True, slots=True)
class RegionAnalysis:
    authoritative_total: int
    detailed_count: int
    mapped_count: int
    unique_pair_count: int
    duplicate_pair_count: int
    reversed_pair_count: int
    involved_triangle_count: int
    involved_vertex_count: int
    regions: tuple[IntersectionRegion, ...]
    build_seconds: float


@dataclass(frozen=True, slots=True)
class RegionDisplacement:
    vertex_index: int
    original: Point
    delta: Point


@dataclass(frozen=True, slots=True)
class RegionCandidate:
    candidate_id: str
    region_id: int
    object_uuid: str
    displacements: tuple[RegionDisplacement, ...]
    local_scale: float
    amplitude_fraction: float
    direction_kind: str
    max_displacement: float
    max_edge_stretch: float
    max_edge_compression: float
    min_area_ratio: float
    max_area_ratio: float
    local_crossings_before: int
    local_crossings_after: int

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "region_id": self.region_id,
            "object_uuid": self.object_uuid,
            "displacements": [{"vertex_index": item.vertex_index,
                "original": item.original, "delta": item.delta}
                for item in self.displacements],
            "local_scale": self.local_scale,
            "amplitude_fraction": self.amplitude_fraction,
            "direction_kind": self.direction_kind,
            "max_displacement": self.max_displacement,
            "max_edge_stretch": self.max_edge_stretch,
            "max_edge_compression": self.max_edge_compression,
            "min_area_ratio": self.min_area_ratio,
            "max_area_ratio": self.max_area_ratio,
            "local_crossings_before": self.local_crossings_before,
            "local_crossings_after": self.local_crossings_after,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "RegionCandidate":
        return cls(
            str(value["candidate_id"]), int(value["region_id"]),
            str(value["object_uuid"]), tuple(RegionDisplacement(
                int(item["vertex_index"]), _point(item["original"]),
                _point(item["delta"])) for item in value["displacements"]),
            float(value["local_scale"]), float(value["amplitude_fraction"]),
            str(value["direction_kind"]), float(value["max_displacement"]),
            float(value["max_edge_stretch"]),
            float(value["max_edge_compression"]),
            float(value["min_area_ratio"]), float(value["max_area_ratio"]),
            int(value["local_crossings_before"]),
            int(value["local_crossings_after"]))


@dataclass(frozen=True, slots=True)
class RegionCandidateBatch:
    schema: str
    job_id: str
    source_snapshot_identity: str
    authoritative_total: int
    region_count: int
    generated_count: int
    locally_rejected_count: int
    skipped_region_count: int
    candidates: tuple[RegionCandidate, ...]
    analysis: dict
    planning_seconds: float
    local_validation_seconds: float

    def to_dict(self) -> dict:
        return {
            "schema": self.schema, "job_id": self.job_id,
            "source_snapshot_identity": self.source_snapshot_identity,
            "authoritative_total": self.authoritative_total,
            "region_count": self.region_count,
            "generated_count": self.generated_count,
            "locally_rejected_count": self.locally_rejected_count,
            "skipped_region_count": self.skipped_region_count,
            "candidates": [item.to_dict() for item in self.candidates],
            "analysis": self.analysis, "planning_seconds": self.planning_seconds,
            "local_validation_seconds": self.local_validation_seconds,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "RegionCandidateBatch":
        if value.get("schema") != "cnx.veyra.region-plan.v1":
            raise ValueError("unsupported VEYRA region-plan schema")
        return cls(
            str(value["schema"]), str(value["job_id"]),
            str(value["source_snapshot_identity"]),
            int(value["authoritative_total"]), int(value["region_count"]),
            int(value["generated_count"]),
            int(value["locally_rejected_count"]),
            int(value["skipped_region_count"]),
            tuple(RegionCandidate.from_dict(item)
                  for item in value["candidates"]), dict(value["analysis"]),
            float(value["planning_seconds"]),
            float(value["local_validation_seconds"]))


def _length(value: Point) -> float:
    return sqrt(sum(component * component for component in value))


def _sub(left: Point, right: Point) -> Point:
    return tuple(a - b for a, b in zip(left, right))


def _add(left: Point, right: Point) -> Point:
    return tuple(a + b for a, b in zip(left, right))


def _mul(value: Point, scalar: float) -> Point:
    return tuple(component * scalar for component in value)


def _dot(left: Point, right: Point) -> float:
    return sum(a * b for a, b in zip(left, right))


def _cross(left: Point, right: Point) -> Point:
    return (left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0])


def _unit(value: Point) -> Point | None:
    length = _length(value)
    return _mul(value, 1.0 / length) if length > 1.0e-15 else None


def _point(value) -> Point:
    result = tuple(map(float, value))
    if len(result) != 3 or not all(isfinite(item) for item in result):
        raise ValueError("VEYRA region point must be finite xyz")
    return result


def _centroid(triangle: RegionTriangle) -> Point:
    return tuple(sum(point[axis] for point in triangle.vertices) / 3.0
                 for axis in range(3))


def _normal_points(points) -> Point:
    return _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))


def _normal(triangle: RegionTriangle) -> Point:
    return _normal_points(triangle.vertices)


def _bounds(triangles: Iterable[RegionTriangle]) -> tuple[Point, Point]:
    points = tuple(point for triangle in triangles for point in triangle.vertices)
    if not points:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    return (tuple(min(point[axis] for point in points) for axis in range(3)),
            tuple(max(point[axis] for point in points) for axis in range(3)))


def _point_bounds(points):
    return tuple((min(point[axis] for point in points),
                  max(point[axis] for point in points)) for axis in range(3))


def _point_bounds_overlap(first, second, tolerance=1.0e-9):
    return all(first[axis][0] <= second[axis][1] + tolerance
               and second[axis][0] <= first[axis][1] + tolerance
               for axis in range(3))


def _edge_scale(triangles: Iterable[RegionTriangle]) -> float:
    values = []
    for triangle in triangles:
        points = triangle.vertices
        values.extend(_length(_sub(points[(index + 1) % 3], points[index]))
                      for index in range(3))
    return sum(values) / len(values) if values else 0.0


def _disjoint_set(values):
    parent = {value: value for value in values}
    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value
    def union(left, right):
        left, right = find(left), find(right)
        if left == right:
            return
        if right < left:
            left, right = right, left
        parent[right] = left
    return parent, find, union


def _side_assignment(seeds, triangles, adjacency):
    involved = sorted({index for seed in seeds for index in seed.key})
    _parent, find, union = _disjoint_set(involved)
    involved_set = set(involved)
    for triangle_index in involved:
        for neighbor in adjacency.get(triangle_index, ()):
            if neighbor in involved_set:
                union(triangle_index, neighbor)
    constraints = {}
    ambiguous = False
    for seed in seeds:
        left, right = find(seed.first), find(seed.second)
        if left == right:
            ambiguous = True
            continue
        constraints.setdefault(left, set()).add(right)
        constraints.setdefault(right, set()).add(left)
    colors = {}
    for root in sorted({find(value) for value in involved}):
        if root in colors:
            continue
        colors[root] = 0
        queue = [root]
        while queue:
            current = queue.pop(0)
            for neighbor in sorted(constraints.get(current, ())):
                expected = 1 - colors[current]
                if neighbor in colors and colors[neighbor] != expected:
                    ambiguous = True
                elif neighbor not in colors:
                    colors[neighbor] = expected
                    queue.append(neighbor)
    side_a = tuple(value for value in involved if colors.get(find(value), 0) == 0)
    side_b = tuple(value for value in involved if colors.get(find(value), 0) == 1)
    if not side_a or not side_b:
        ambiguous = True
    return side_a, side_b, ambiguous


def build_regions(value: dict) -> RegionAnalysis:
    """Build topological pair components without inventing solver seeds."""
    started = perf_counter()
    triangle_rows = value.get("triangles", ())
    triangles = {int(row["triangle_index"]): RegionTriangle(
        str(row["object_uuid"]), int(row["triangle_index"]),
        tuple(map(int, row["vertex_indices"])),
        tuple(tuple(map(float, point)) for point in row["vertices"]))
        for row in triangle_rows}
    raw_pairs = [tuple(map(int, row)) for row in value.get("pairs", ())]
    normalized = [tuple(sorted(pair)) for pair in raw_pairs]
    unique_keys = sorted(set(normalized))
    seeds = tuple(RegionSeed(*pair) for pair in unique_keys
                  if pair[0] in triangles and pair[1] in triangles
                  and triangles[pair[0]].object_uuid ==
                  triangles[pair[1]].object_uuid)
    reversed_count = sum(pair[0] > pair[1] for pair in raw_pairs)

    by_vertex = {}
    for triangle in triangles.values():
        for vertex in triangle.vertex_indices:
            key = (triangle.object_uuid, vertex)
            by_vertex.setdefault(key, set()).add(triangle.triangle_index)
    adjacency = {index: set() for index in triangles}
    for linked in by_vertex.values():
        for index in linked:
            adjacency[index].update(linked - {index})

    pair_neighbors = [set() for _seed in seeds]
    vertex_to_pairs = {}
    for pair_index, seed in enumerate(seeds):
        core_vertices = {
            vertex for triangle_index in seed.key
            for vertex in triangles[triangle_index].vertex_indices}
        object_uuid = triangles[seed.first].object_uuid
        for vertex_index in core_vertices:
            vertex_to_pairs.setdefault(
                (object_uuid, vertex_index), set()).add(pair_index)
    for linked in vertex_to_pairs.values():
        for pair_index in linked:
            pair_neighbors[pair_index].update(linked - {pair_index})

    unseen = set(range(len(seeds)))
    components = []
    while unseen:
        first = min(unseen); unseen.remove(first)
        members = {first}; queue = [first]
        while queue:
            current = queue.pop(0)
            for neighbor in sorted(pair_neighbors[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor); members.add(neighbor)
                    queue.append(neighbor)
        components.append(tuple(seeds[index] for index in sorted(members)))

    regions = []
    for region_id, component in enumerate(components):
        indices = tuple(sorted({index for seed in component for index in seed.key}))
        region_triangles = tuple(triangles[index] for index in indices)
        vertices = tuple(sorted({vertex for triangle in region_triangles
                                 for vertex in triangle.vertex_indices}))
        side_a, side_b, ambiguous = _side_assignment(
            component, triangles, adjacency)
        regions.append(IntersectionRegion(
            region_id, region_triangles[0].object_uuid, component, indices,
            vertices, _bounds(region_triangles), _edge_scale(region_triangles),
            side_a, side_b, ambiguous))
    regions.sort(key=lambda item: (-len(item.seeds), item.object_uuid,
                                   item.triangle_indices))
    regions = [IntersectionRegion(
        index, item.object_uuid, item.seeds, item.triangle_indices,
        item.vertex_indices, item.bounds, item.local_edge_scale,
        item.side_a, item.side_b, item.ambiguous_sides)
        for index, item in enumerate(regions)]
    involved_triangles = {index for seed in seeds for index in seed.key}
    involved_vertices = {
        (triangles[index].object_uuid, vertex)
        for index in involved_triangles
        for vertex in triangles[index].vertex_indices}
    return RegionAnalysis(
        int(value.get("authoritative_total", len(raw_pairs))),
        int(value.get("detailed_count", len(raw_pairs))),
        int(value.get("mapped_count", len(raw_pairs))), len(unique_keys),
        len(raw_pairs) - len(unique_keys), reversed_count,
        len(involved_triangles), len(involved_vertices), tuple(regions),
        perf_counter() - started)


def analysis_dict(analysis: RegionAnalysis) -> dict:
    return {
        "authoritative_total": analysis.authoritative_total,
        "detailed_count": analysis.detailed_count,
        "mapped_count": analysis.mapped_count,
        "unique_pair_count": analysis.unique_pair_count,
        "duplicate_pair_count": analysis.duplicate_pair_count,
        "reversed_pair_count": analysis.reversed_pair_count,
        "involved_triangle_count": analysis.involved_triangle_count,
        "involved_vertex_count": analysis.involved_vertex_count,
        "region_count": len(analysis.regions),
        "build_seconds": analysis.build_seconds,
        "regions": [{
            "region_id": item.region_id, "pairs": len(item.seeds),
            "triangles": len(item.triangle_indices),
            "vertices": len(item.vertex_indices), "bounds": item.bounds,
            "local_edge_scale": item.local_edge_scale,
            "side_a_triangles": len(item.side_a),
            "side_b_triangles": len(item.side_b),
            "ambiguous_sides": item.ambiguous_sides,
        } for item in analysis.regions],
    }


def _edge_adjacency(triangles):
    by_edge = {}
    for triangle in triangles.values():
        indices = triangle.vertex_indices
        for offset in range(3):
            edge = (triangle.object_uuid,) + tuple(sorted(
                (indices[offset], indices[(offset + 1) % 3])))
            by_edge.setdefault(edge, set()).add(triangle.triangle_index)
    adjacency = {index: set() for index in triangles}
    for linked in by_edge.values():
        for index in linked:
            adjacency[index].update(linked - {index})
    return adjacency


def expand_patch(core, adjacency, *, rings=2, blocked=(), maximum=2048):
    """Return triangle ring distances with an exact bounded frontier."""
    distances = {int(index): 0 for index in core}
    blocked = set(map(int, blocked))
    frontier = sorted(distances)
    for distance in range(1, max(0, int(rings)) + 1):
        following = []
        for index in frontier:
            for neighbor in sorted(adjacency.get(index, ())):
                if neighbor in blocked or neighbor in distances:
                    continue
                if len(distances) >= maximum:
                    return distances
                distances[neighbor] = distance
                following.append(neighbor)
        frontier = following
        if not frontier:
            break
    return distances


def _patch_weights(distances, triangles, rings):
    weights = {}
    for triangle_index, distance in distances.items():
        weight = max(0.0, 1.0 - distance / max(1, rings))
        for vertex in triangles[triangle_index].vertex_indices:
            weights[vertex] = max(weights.get(vertex, 0.0), weight)
    # One conservative scalar smoothing pass. Ring-N boundary remains fixed.
    neighbors = {}
    for triangle_index in distances:
        indices = triangles[triangle_index].vertex_indices
        for offset in range(3):
            left, right = indices[offset], indices[(offset + 1) % 3]
            neighbors.setdefault(left, set()).add(right)
            neighbors.setdefault(right, set()).add(left)
    result = {}
    for vertex, weight in weights.items():
        linked = neighbors.get(vertex, ())
        average = (sum(weights.get(item, 0.0) for item in linked) / len(linked)
                   if linked else weight)
        result[vertex] = (0.75 * weight + 0.25 * average
                          if weight > 0.0 else 0.0)
    return result


def _candidate_directions(region, triangles):
    side_a = set(region.side_a)
    centroid_vectors = []
    for seed in region.seeds:
        first, second = triangles[seed.first], triangles[seed.second]
        value = _sub(_centroid(first), _centroid(second))
        centroid_vectors.append(value if seed.first in side_a else _mul(value, -1.0))
    centroid = _unit(tuple(sum(value[axis] for value in centroid_vectors)
                           for axis in range(3)))
    if centroid is None:
        return ()
    normal_a = _unit(tuple(sum(_normal(triangles[index])[axis]
                               for index in region.side_a)
                           for axis in range(3)))
    normal_b = _unit(tuple(sum(_normal(triangles[index])[axis]
                               for index in region.side_b)
                           for axis in range(3)))
    values = [("centroid", centroid)]
    for name, value in (("side_a_normal", normal_a),
                        ("side_b_normal", _mul(normal_b, -1.0)
                         if normal_b else None)):
        if value is None:
            continue
        if _dot(value, centroid) < 0.0:
            value = _mul(value, -1.0)
        if all(abs(_dot(value, existing)) < 0.985
               for _existing_name, existing in values):
            values.append((name, value))
    return tuple(values[:3])


def _coordinates(triangles, object_uuid):
    result = {}
    for triangle in triangles.values():
        if triangle.object_uuid != object_uuid:
            continue
        for vertex, point in zip(triangle.vertex_indices, triangle.vertices):
            result.setdefault(vertex, point)
    return result


def _candidate_points(triangle, planned):
    return tuple(_add(point, planned.get(vertex, (0.0, 0.0, 0.0)))
                 for vertex, point in zip(triangle.vertex_indices,
                                          triangle.vertices))


def _crossing(first, second) -> bool:
    return (triangles_strictly_cross(first, second)
            or triangles_coplanar_overlap(first, second))


def _candidate_metrics(planned, patch_indices, triangles):
    minimum_edge_ratio = float("inf"); maximum_edge_ratio = 0.0
    minimum_area_ratio = float("inf"); maximum_area_ratio = 0.0
    for triangle_index in patch_indices:
        triangle = triangles[triangle_index]
        before = triangle.vertices
        after = _candidate_points(triangle, planned)
        before_normal = _normal_points(before); after_normal = _normal_points(after)
        before_area = _length(before_normal); after_area = _length(after_normal)
        if before_area <= 1.0e-15 or after_area <= 1.0e-15:
            return None
        area_ratio = after_area / before_area
        minimum_area_ratio = min(minimum_area_ratio, area_ratio)
        maximum_area_ratio = max(maximum_area_ratio, area_ratio)
        if _dot(before_normal, after_normal) <= 0.25 * before_area * after_area:
            return None
        for offset in range(3):
            before_edge = _length(_sub(
                before[(offset + 1) % 3], before[offset]))
            after_edge = _length(_sub(
                after[(offset + 1) % 3], after[offset]))
            if before_edge <= 1.0e-15:
                return None
            ratio = after_edge / before_edge
            minimum_edge_ratio = min(minimum_edge_ratio, ratio)
            maximum_edge_ratio = max(maximum_edge_ratio, ratio)
    if (minimum_edge_ratio < 0.80 or maximum_edge_ratio > 1.20
            or minimum_area_ratio < 0.60 or maximum_area_ratio > 1.50):
        return None
    return (minimum_edge_ratio, maximum_edge_ratio,
            minimum_area_ratio, maximum_area_ratio)


def _patch_crossings(patch_indices, triangles, planned):
    values = sorted(patch_indices)
    before = 0; after = 0
    for offset, left_index in enumerate(values):
        left = triangles[left_index]
        for right_index in values[offset + 1:]:
            right = triangles[right_index]
            if set(left.vertex_indices).intersection(right.vertex_indices):
                continue
            # Cheap pair AABB gate before exact narrow phase.
            left_before, right_before = left.vertices, right.vertices
            left_after = _candidate_points(left, planned)
            right_after = _candidate_points(right, planned)
            if (_point_bounds_overlap(
                    _point_bounds(left_before), _point_bounds(right_before))
                    and _crossing(left_before, right_before)):
                before += 1
            if (_point_bounds_overlap(
                    _point_bounds(left_after), _point_bounds(right_after))
                    and _crossing(left_after, right_after)):
                after += 1
    return before, after


def solve_region_candidates(value, *, progress=None, cancelled=None):
    """Plan a small ranked batch; Blender/Lumen remains acceptance authority."""
    started = perf_counter()
    if value.get("schema") != "cnx.veyra.region-input.v1":
        raise ValueError("unsupported VEYRA region input schema")
    analysis = build_regions(value)
    triangle_rows = value.get("triangles", ())
    triangles = {int(row["triangle_index"]): RegionTriangle(
        str(row["object_uuid"]), int(row["triangle_index"]),
        tuple(map(int, row["vertex_indices"])),
        tuple(_point(point) for point in row["vertices"]))
        for row in triangle_rows}
    adjacency = _edge_adjacency(triangles)
    cumulative = {
        (str(item["object_uuid"]), int(item["vertex_index"])):
            _point(item["delta"])
        for item in value.get("cumulative_displacements", ())}
    generated = 0; locally_rejected = 0; skipped = 0
    accepted = []; local_seconds = 0.0
    # Prefer compact, dense, unambiguous regions. Very large sheets are not
    # silently moved as one patch in V1.
    regions = sorted(analysis.regions, key=lambda item: (
        item.ambiguous_sides, len(item.triangle_indices) > 256,
        -(len(item.seeds) / max(1, len(item.triangle_indices))),
        -len(item.seeds), item.region_id))
    for region_offset, region in enumerate(regions, start=1):
        if cancelled and cancelled():
            from .solver import VeyraCancelled
            raise VeyraCancelled("VEYRA region planning cancelled")
        if progress:
            progress(VeyraStep.SOLVING_REPAIR_PLAN,
                     region_offset, len(regions),
                     f"Regions {region_offset} / {len(regions)}")
        if region.ambiguous_sides or len(region.triangle_indices) > 256:
            skipped += 1; continue
        coordinates = _coordinates(triangles, region.object_uuid)
        side_a_core, side_b_core = set(region.side_a), set(region.side_b)
        side_a_patch = expand_patch(
            side_a_core, adjacency, rings=2, blocked=side_b_core)
        side_b_patch = expand_patch(
            side_b_core, adjacency, rings=2, blocked=side_a_core)
        if set(side_a_patch).intersection(side_b_patch):
            skipped += 1; continue
        weights_a = _patch_weights(side_a_patch, triangles, 2)
        weights_b = _patch_weights(side_b_patch, triangles, 2)
        if set(weights_a).intersection(weights_b):
            skipped += 1; continue
        directions = _candidate_directions(region, triangles)
        if not directions:
            skipped += 1; continue
        patch_indices = set(side_a_patch) | set(side_b_patch)
        for direction_kind, direction in directions:
            for amplitude_fraction in (0.02, 0.04, 0.08):
                generated += 1
                amplitude = region.local_edge_scale * amplitude_fraction
                planned = {}
                for vertex, weight in weights_a.items():
                    if weight > 0.0:
                        planned[vertex] = _mul(direction, amplitude * .5 * weight)
                for vertex, weight in weights_b.items():
                    if weight > 0.0:
                        planned[vertex] = _mul(direction, -amplitude * .5 * weight)
                if (not planned or max(map(_length, planned.values()), default=0.0)
                        > region.local_edge_scale * 0.08 + 1.0e-15):
                    locally_rejected += 1; continue
                if any(_length(_add(cumulative.get(
                                    (region.object_uuid, vertex),
                                    (0.0, 0.0, 0.0)),
                                    delta)) > region.local_edge_scale * 0.20
                       for vertex, delta in planned.items()):
                    locally_rejected += 1; continue
                check_started = perf_counter()
                metrics = _candidate_metrics(planned, patch_indices, triangles)
                if metrics is None:
                    local_seconds += perf_counter() - check_started
                    locally_rejected += 1; continue
                before, after = _patch_crossings(
                    patch_indices, triangles, planned)
                local_seconds += perf_counter() - check_started
                if after > before:
                    locally_rejected += 1; continue
                minimum_edge, maximum_edge, minimum_area, maximum_area = metrics
                accepted.append(RegionCandidate(
                    f"r{region.region_id}-{direction_kind}-{amplitude_fraction:.2f}",
                    region.region_id, region.object_uuid,
                    tuple(RegionDisplacement(vertex, coordinates[vertex], delta)
                          for vertex, delta in sorted(planned.items())),
                    region.local_edge_scale, amplitude_fraction, direction_kind,
                    max(map(_length, planned.values()), default=0.0),
                    maximum_edge, minimum_edge, minimum_area, maximum_area,
                    before, after))
        # Limit expensive Lumen candidates to the best compact region in one
        # fresh-diagnostics pass. Accepted geometry always rebuilds regions.
        if accepted:
            break
    accepted.sort(key=lambda item: (
        item.local_crossings_after - item.local_crossings_before,
        item.max_displacement, item.region_id, item.direction_kind,
        item.amplitude_fraction, item.candidate_id))
    rejected_ids = set(map(str, value.get("rejected_candidate_ids", ())))
    accepted = [item for item in accepted
                if item.candidate_id not in rejected_ids]
    # Preserve scale coverage: six tiny candidates are less useful to the
    # authoritative validator than two directions at each bounded amplitude.
    selected = []
    for amplitude in (0.02, 0.04, 0.08):
        selected.extend([item for item in accepted
                         if item.amplitude_fraction == amplitude][:2])
    accepted = selected
    planning_seconds = perf_counter() - started
    return RegionCandidateBatch(
        "cnx.veyra.region-plan.v1", str(value["job_id"]),
        str(value["source_snapshot_identity"]), analysis.authoritative_total,
        len(analysis.regions), generated, locally_rejected, skipped,
        tuple(accepted), analysis_dict(analysis), planning_seconds,
        local_seconds)
