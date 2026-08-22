# SPDX-License-Identifier: GPL-3.0-or-later
"""Conservative, Blender-independent planning for VEYRA topology welds."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import product
import math
from typing import Callable, Hashable, Iterable


@dataclass(frozen=True, slots=True)
class WeldVertex:
    index: int
    coordinate: tuple[float, float, float]
    island: int
    boundary: bool
    attribute_signature: Hashable
    adjacent_vertices: frozenset[int] = frozenset()
    adjacent_faces: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class SafeWeldPlan:
    object_uuid: str
    local_scale: float
    tolerance: float
    clusters: tuple[tuple[int, ...], ...]
    skipped: tuple[tuple[tuple[int, ...], str], ...]

    @property
    def merged_vertices(self) -> int:
        return sum(len(cluster) - 1 for cluster in self.clusters)

    @property
    def skip_reasons(self) -> dict[str, int]:
        return dict(Counter(reason for _cluster, reason in self.skipped))


class _UnionFind:
    def __init__(self, values: Iterable[int]):
        self.parent = {value: value for value in values}

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left = self.find(left)
        right = self.find(right)
        if left != right:
            self.parent[max(left, right)] = min(left, right)


def conservative_tolerance(local_scale: float) -> float:
    """Return a scale-aware tolerance limited to floating-point noise."""
    if not math.isfinite(local_scale) or local_scale <= 0.0:
        raise ValueError("local mesh scale must be finite and positive")
    return max(1.0e-12, local_scale * 1.0e-9)


def _sub(left, right):
    return tuple(a - b for a, b in zip(left, right))


def _dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def _unit(value):
    length = math.sqrt(_dot(value, value))
    return (tuple(component / length for component in value)
            if length > 1.0e-15 else None)


def _project_perpendicular(value, tangent):
    return tuple(component - tangent[axis] * _dot(value, tangent)
                 for axis, component in enumerate(value))


def _continuous_boundary_seam(cluster, *, by_index, candidate_by_vertex) -> bool:
    """Prove that a coincident pair joins opposite sides of one boundary seam.

    Coincidence alone cannot distinguish an import seam from lining or a
    decorative layer.  The pair therefore needs a second coincident boundary
    pair along the seam and the two surface interiors must continue away on
    opposite sides of that seam.  Missing or multi-sheet evidence fails closed.
    """
    if len(cluster) != 2:
        return False
    left, right = (by_index[index] for index in cluster)
    if left.island == right.island or not left.boundary or not right.boundary:
        return False
    island_pair = frozenset((left.island, right.island))
    linked_clusters = []
    for left_neighbor in left.adjacent_vertices:
        neighbor_cluster = candidate_by_vertex.get(left_neighbor)
        if neighbor_cluster is None or neighbor_cluster == cluster:
            continue
        rows = tuple(by_index[index] for index in neighbor_cluster)
        if (len(rows) == 2 and frozenset(row.island for row in rows) == island_pair
                and any(row.index in right.adjacent_vertices for row in rows)):
            linked_clusters.append(neighbor_cluster)
    if not linked_clusters:
        return False

    for neighbor_cluster in sorted(set(linked_clusters)):
        neighbor_by_island = {by_index[index].island: by_index[index]
                              for index in neighbor_cluster}
        seam_vector = _sub(
            neighbor_by_island[left.island].coordinate, left.coordinate)
        tangent = _unit(seam_vector)
        if tangent is None:
            continue
        inward = []
        for row in (left, right):
            vectors = []
            for neighbor_index in row.adjacent_vertices:
                if candidate_by_vertex.get(neighbor_index) == neighbor_cluster:
                    continue
                vector = _project_perpendicular(
                    _sub(by_index[neighbor_index].coordinate, row.coordinate),
                    tangent)
                unit = _unit(vector)
                if unit is not None:
                    vectors.append(unit)
            if not vectors:
                break
            inward.append(_unit(tuple(sum(vector[axis] for vector in vectors)
                                      for axis in range(3))))
        if len(inward) == 2 and all(inward) and _dot(*inward) <= -0.25:
            return True
    return False


def plan_safe_welds(
        object_uuid: str, vertices: Iterable[WeldVertex], *,
        local_scale: float, eligible_indices: Iterable[int] | None = None,
) -> SafeWeldPlan:
    """Plan explicit near-exact weld IDs without radius-expanding selection.

    Same-island candidates must be boundary vertices. All candidates require
    identical point-domain attributes. Disconnected islands require geometric
    proof of a coherent boundary seam; object membership alone is not intent.
    """
    if not object_uuid:
        raise ValueError("an object UUID is required")
    rows = tuple(sorted(vertices, key=lambda item: item.index))
    if len({row.index for row in rows}) != len(rows):
        raise ValueError("vertex indices must be unique")
    tolerance = conservative_tolerance(local_scale)
    cells: dict[tuple[int, int, int], list[WeldVertex]] = defaultdict(list)
    union = _UnionFind(row.index for row in rows)
    by_index = {row.index: row for row in rows}
    eligible = (None if eligible_indices is None
                else frozenset(map(int, eligible_indices)))
    offsets = tuple(product((-1, 0, 1), repeat=3))
    for row in rows:
        if not all(math.isfinite(value) for value in row.coordinate):
            continue
        cell = tuple(math.floor(value / tolerance) for value in row.coordinate)
        for offset in offsets:
            neighbor = tuple(cell[axis] + offset[axis] for axis in range(3))
            for other in cells.get(neighbor, ()):
                if math.dist(row.coordinate, other.coordinate) <= tolerance:
                    union.union(row.index, other.index)
        cells[cell].append(row)
    groups: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        groups[union.find(row.index)].append(row.index)
    accepted = []
    skipped = []
    provisional = []
    for cluster in sorted((tuple(values) for values in groups.values()
                           if len(values) > 1)):
        items = tuple(by_index[index] for index in cluster)
        island_count = len({item.island for item in items})
        if (island_count == 1 and not all(item.boundary for item in items)):
            reason = "NON_BOUNDARY"
        elif len({item.attribute_signature for item in items}) != 1:
            reason = "POINT_ATTRIBUTE_CONFLICT"
        elif any(right.index in left.adjacent_vertices
                 for left in items for right in items if left != right):
            reason = "EXISTING_EDGE"
        elif any(left.adjacent_faces.intersection(right.adjacent_faces)
                 for offset, left in enumerate(items)
                 for right in items[offset + 1:]):
            reason = "SHARED_FACE"
        elif eligible is not None and not eligible.intersection(cluster):
            reason = "NOT_DIAGNOSED"
        else:
            provisional.append(cluster)
            continue
        skipped.append((cluster, reason))
    candidate_by_vertex = {index: cluster for cluster in provisional
                           for index in cluster}
    for cluster in provisional:
        items = tuple(by_index[index] for index in cluster)
        if len({item.island for item in items}) > 1 and not _continuous_boundary_seam(
                cluster, by_index=by_index,
                candidate_by_vertex=candidate_by_vertex):
            skipped.append((cluster, "UNPROVEN_BOUNDARY_SEAM"))
        else:
            accepted.append(cluster)
    return SafeWeldPlan(
        object_uuid, float(local_scale), tolerance,
        tuple(accepted), tuple(skipped))


class TopologyTransaction:
    """Install a working mesh and restore the untouched original on rollback."""

    def __init__(self, original, working, *, install: Callable[[object], None],
                 dispose: Callable[[object], None], digest: Callable[[object], str]):
        self.original = original
        self.working = working
        self._install = install
        self._dispose = dispose
        self._digest = digest
        self.original_digest = digest(original)
        self.active = False

    def begin(self) -> None:
        if self.active:
            raise RuntimeError("topology transaction is already active")
        self._install(self.working)
        self.active = True

    def accept(self) -> None:
        if not self.active:
            raise RuntimeError("topology transaction is not active")
        self._dispose(self.original)
        self.active = False

    def rollback(self) -> None:
        if not self.active:
            return
        self._install(self.original)
        if self._digest(self.original) != self.original_digest:
            raise RuntimeError("topology rollback structural verification failed")
        self.active = False
        self._dispose(self.working)
