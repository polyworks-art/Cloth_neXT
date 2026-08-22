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


def plan_safe_welds(
        object_uuid: str, vertices: Iterable[WeldVertex], *,
        local_scale: float, allow_disconnected_islands: bool = False,
) -> SafeWeldPlan:
    """Plan explicit near-exact weld IDs without radius-expanding selection.

    Same-island candidates must be boundary vertices. All candidates require
    identical point-domain attributes. Disconnected islands are rejected unless
    the caller has an explicit invariant that a deformable is one connected
    sheet.
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
        elif island_count > 1 and not allow_disconnected_islands:
            reason = "DISCONNECTED_SHEETS"
        else:
            accepted.append(cluster)
            continue
        skipped.append((cluster, reason))
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
