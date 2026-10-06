# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure data model and validation for cross-object solver attachments."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import json
import math


SUPPORTED_ROLES = frozenset({"CLOTH", "SOFT_BODY"})
SUPPORTED_TARGET_ROLES = SUPPORTED_ROLES | frozenset({"COLLIDER"})
DEFAULT_STIFFNESS = 1.0


class AttachmentError(ValueError):
    """An attachment cannot be represented safely by the solver."""


@dataclass(frozen=True, slots=True)
class AttachmentPoint:
    source_index: int
    target_triangle: tuple[int, int, int]
    target_weights: tuple[float, float, float]
    source_point: tuple[float, float, float]
    target_point: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ObjectAttachment:
    identifier: str
    name: str
    source_uuid: str
    target_uuid: str
    source_role: str
    target_role: str
    stiffness: float
    points: tuple[AttachmentPoint, ...]
    # Explicit Sewing can join two paths of the same dynamic cloth. Regular
    # Object Attachments retain their distinct-object validation.
    allow_same_object: bool = False


@dataclass(frozen=True, slots=True)
class SelectionTopology:
    kind: str
    paths: tuple[tuple[int, ...], ...] = ()


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _mul(a, value):
    return tuple(x * value for x in a)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def closest_point_on_triangle(point, a, b, c):
    """Closest point and barycentric weights (Ericson region tests)."""
    point, a, b, c = map(lambda row: tuple(map(float, row)), (point, a, b, c))
    ab, ac, ap = _sub(b, a), _sub(c, a), _sub(point, a)
    d1, d2 = _dot(ab, ap), _dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a, (1.0, 0.0, 0.0)
    bp = _sub(point, b)
    d3, d4 = _dot(ab, bp), _dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b, (0.0, 1.0, 0.0)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return _add(a, _mul(ab, v)), (1.0 - v, v, 0.0)
    cp = _sub(point, c)
    d5, d6 = _dot(ab, cp), _dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c, (0.0, 0.0, 1.0)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return _add(a, _mul(ac, w)), (1.0 - w, 0.0, w)
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        edge = _sub(c, b)
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return _add(b, _mul(edge, w)), (0.0, 1.0 - w, w)
    denominator = 1.0 / (va + vb + vc)
    v, w = vb * denominator, vc * denominator
    return _add(a, _add(_mul(ab, v), _mul(ac, w))), (1.0 - v - w, v, w)


def closest_surface_point(point, vertices, triangles):
    best = None
    for triangle in triangles:
        closest, weights = closest_point_on_triangle(
            point, *(vertices[index] for index in triangle))
        delta = _sub(closest, point)
        candidate = (_dot(delta, delta), tuple(map(int, triangle)),
                     weights, closest)
        if best is None or candidate[0] < best[0]:
            best = candidate
    if best is None:
        raise AttachmentError("attachment target has no usable surface triangles")
    return best[1], best[2], best[3]


class VertexSearch:
    """Balanced 3D k-d tree with deterministic index tie-breaking."""

    def __init__(self, vertices, indices):
        def build(rows, depth=0):
            if not rows:
                return None
            axis = depth % 3
            rows.sort(key=lambda row: (row[0][axis], row[1]))
            middle = len(rows) // 2
            return (rows[middle], axis, build(rows[:middle], depth + 1),
                    build(rows[middle + 1:], depth + 1))
        self.root = build([(tuple(vertices[i]), i) for i in indices])
        if self.root is None:
            raise AttachmentError("Target vertex group is empty")

    def nearest(self, point):
        best = (math.inf, math.inf)

        def visit(node):
            nonlocal best
            if node is None:
                return
            (position, index), axis, left, right = node
            distance = sum((a - b) ** 2 for a, b in zip(point, position))
            best = min(best, (distance, index))
            delta = point[axis] - position[axis]
            near, far = (left, right) if delta < 0 else (right, left)
            visit(near)
            if delta * delta <= best[0]:
                visit(far)
        visit(self.root)
        return best[1]


def spatial_vertex_mapping(source_vertices, source_indices,
                           target_vertices, target_indices):
    """Map selected source vertices to selected targets in world space.

    A deterministic global greedy pass avoids click-order dependence and uses
    each target at most once while targets remain.  If there are more source
    vertices than targets, the remaining sources reuse their nearest target;
    this matches the solver's one-constraint-per-source representation.
    """
    sources = sorted({int(index) for index in source_indices}, key=lambda index: (
        tuple(map(float, source_vertices[index])), index))
    targets = sorted({int(index) for index in target_indices}, key=lambda index: (
        tuple(map(float, target_vertices[index])), index))
    if not sources:
        raise AttachmentError("Source vertex selection is empty")
    if not targets:
        raise AttachmentError("Target vertex selection is empty")

    def distance(source, target):
        return sum((float(a) - float(b)) ** 2 for a, b in zip(
            source_vertices[source], target_vertices[target]))

    available_sources, available_targets, result = set(sources), set(targets), []
    if max(len(sources), len(targets)) <= 256:
        # Better global choices for the small boundary selections artists
        # normally make.  The cap prevents cubic behaviour on dense meshes.
        while available_sources and available_targets:
            _, source, target = min(
                (distance(source, target), source, target)
                for source in available_sources for target in available_targets)
            result.append((source, target))
            available_sources.remove(source)
            available_targets.remove(target)
    else:
        # Deterministic O(source*target) bounded-memory fallback.
        for source in sources:
            if not available_targets:
                break
            target = min(available_targets,
                         key=lambda index: (distance(source, index), index))
            result.append((source, target))
            available_sources.remove(source)
            available_targets.remove(target)
    for source in sorted(available_sources):
        target = min(targets, key=lambda index: (distance(source, index), index))
        result.append((source, target))
    return tuple(sorted(result))


def selected_topology(vertices, indices, edges) -> SelectionTopology:
    """Classify and order components in the original selected-edge graph."""
    selected = set(map(int, indices))
    if not selected:
        return SelectionTopology("EMPTY")
    adjacency = {index: set() for index in selected}
    for edge in edges:
        a, b = map(int, edge)
        if a in selected and b in selected:
            adjacency[a].add(b)
            adjacency[b].add(a)

    def vertex_key(index):
        return tuple(map(float, vertices[index])), index

    components = []
    unseen = set(selected)
    while unseen:
        seed = min(unseen, key=vertex_key)
        component, stack = set(), [seed]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            unseen.discard(current)
            stack.extend(adjacency[current] - component)
        components.append(component)
    components.sort(key=lambda component: min(vertex_key(i) for i in component))

    paths = []
    for component in components:
        if len(component) == 1:
            paths.append((next(iter(component)),))
            continue
        degrees = {index: len(adjacency[index] & component) for index in component}
        if all(degree == 2 for degree in degrees.values()):
            return SelectionTopology("LOOP")
        endpoints = [index for index, degree in degrees.items() if degree == 1]
        if (len(endpoints) != 2
                or any(degree not in {1, 2} for degree in degrees.values())):
            return SelectionTopology("BRANCHED")
        start = min(endpoints, key=vertex_key)
        ordered, previous, current = [], None, start
        while current is not None:
            ordered.append(current)
            candidates = (adjacency[current] & component) - ({previous} if previous is not None else set())
            following = min(candidates, key=vertex_key) if candidates else None
            previous, current = current, following
        if len(ordered) != len(component):
            return SelectionTopology("BRANCHED")
        paths.append(tuple(ordered))
    return SelectionTopology("PATHS", tuple(paths))


def _normalized_arc(vertices, path):
    if len(path) == 1:
        return (0.0,)
    lengths = [0.0]
    for first, second in zip(path, path[1:]):
        lengths.append(lengths[-1] + math.sqrt(sum(
            (float(a) - float(b)) ** 2
            for a, b in zip(vertices[first], vertices[second]))))
    if lengths[-1] <= 1.0e-12:
        return tuple(index / (len(path) - 1) for index in range(len(path)))
    return tuple(value / lengths[-1] for value in lengths)


def ordered_path_mapping(source_vertices, source_path,
                         target_vertices, target_path, *, flipped=None):
    """Return the lower-cost whole-path orientation with monotone pairing."""
    source_arc = _normalized_arc(source_vertices, source_path)

    def candidate(oriented_target):
        target_arc = _normalized_arc(target_vertices, oriented_target)
        mapping = []
        previous = 0
        for source, parameter in zip(source_path, source_arc):
            target_offset = min(
                range(previous, len(oriented_target)),
                key=lambda offset: (abs(parameter - target_arc[offset]), offset))
            previous = target_offset
            mapping.append((source, oriented_target[target_offset]))
        cost = sum(sum((float(a) - float(b)) ** 2 for a, b in zip(
            source_vertices[source], target_vertices[target]))
                   for source, target in mapping)
        return cost, tuple(mapping)

    forward = candidate(tuple(target_path))
    reverse = candidate(tuple(reversed(target_path)))
    if flipped is not None:
        return (reverse if flipped else forward)[1]
    return min((forward, reverse), key=lambda row: (row[0], row[1]))[1]


def topology_vertex_mapping(source_vertices, source_indices, source_edges,
                            target_vertices, target_indices, target_edges):
    """Use ordered original-mesh paths, isolated from the generic fallback."""
    source_topology = selected_topology(
        source_vertices, source_indices, source_edges)
    target_topology = selected_topology(
        target_vertices, target_indices, target_edges)
    if source_topology.kind == "EMPTY":
        raise AttachmentError("Source vertex selection is empty")
    if target_topology.kind == "EMPTY":
        raise AttachmentError("Target vertex selection is empty")
    if "LOOP" in {source_topology.kind, target_topology.kind}:
        raise AttachmentError(
            "Closed-loop Attachment selections are not supported; select an open path")
    if (source_topology.kind != "PATHS" or target_topology.kind != "PATHS"
            or len(source_topology.paths) != len(target_topology.paths)):
        return spatial_vertex_mapping(
            source_vertices, source_indices, target_vertices, target_indices)

    def centroid(vertices, path):
        return tuple(sum(float(vertices[index][axis]) for index in path) / len(path)
                     for axis in range(3))

    source_centers = tuple(centroid(source_vertices, path)
                           for path in source_topology.paths)
    target_centers = tuple(centroid(target_vertices, path)
                           for path in target_topology.paths)
    component_mapping = spatial_vertex_mapping(
        source_centers, range(len(source_centers)),
        target_centers, range(len(target_centers)))
    result = []
    for source_component, target_component in component_mapping:
        result.extend(ordered_path_mapping(
            source_vertices, source_topology.paths[source_component],
            target_vertices, target_topology.paths[target_component]))
    return tuple(sorted(result))


def shortest_mesh_path(vertices, edges, start, end, *, boundary_edges=(),
                       interior_penalty=1.0):
    """Deterministic geometric shortest path over original mesh edges."""
    start, end = int(start), int(end)
    if start == end:
        return (start,)
    adjacency = {index: [] for index in range(len(vertices))}
    boundary = {tuple(sorted(map(int, edge))) for edge in boundary_edges}
    for edge in edges:
        a, b = map(int, edge)
        distance = math.sqrt(sum((float(x) - float(y)) ** 2
                                 for x, y in zip(vertices[a], vertices[b])))
        if boundary and tuple(sorted((a, b))) not in boundary:
            distance *= max(1.0, float(interior_penalty))
        adjacency[a].append((b, distance))
        adjacency[b].append((a, distance))
    distances = {start: 0.0}
    routes = {start: (start,)}
    queue = [(0.0, (start,), start)]
    while queue:
        distance, route, current = heapq.heappop(queue)
        if (distance, route) != (distances.get(current), routes.get(current)):
            continue
        if current == end:
            return route
        for neighbor, edge_length in sorted(adjacency[current]):
            candidate = distance + edge_length
            candidate_route = route + (neighbor,)
            if (candidate, candidate_route) < (
                    distances.get(neighbor, math.inf), routes.get(neighbor, ())):
                distances[neighbor] = candidate
                routes[neighbor] = candidate_route
                heapq.heappush(queue, (candidate, candidate_route, neighbor))
    raise AttachmentError("Selected path endpoints are disconnected")


def topology_fingerprint(vertex_count: int, triangles) -> str:
    canonical = {
        "version": 1,
        "vertex_count": int(vertex_count),
        "triangles": [list(map(int, triangle)) for triangle in triangles],
    }
    return hashlib.sha256(json.dumps(
        canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def validate_attachment(attachment: ObjectAttachment, *,
                        source_vertex_count: int,
                        target_vertex_count: int) -> None:
    if attachment.source_role not in SUPPORTED_ROLES:
        raise AttachmentError("source must be Cloth or Soft Body")
    if attachment.target_role not in SUPPORTED_TARGET_ROLES:
        raise AttachmentError("target must be Cloth, Soft Body or Collider")
    if not attachment.source_uuid or not attachment.target_uuid:
        raise AttachmentError("source and target UUIDs are required")
    if (attachment.source_uuid == attachment.target_uuid
            and not attachment.allow_same_object):
        raise AttachmentError("source and target must be different objects")
    if not math.isfinite(attachment.stiffness) or attachment.stiffness < 0.0:
        raise AttachmentError("stiffness must be a finite non-negative value")
    if not attachment.points:
        raise AttachmentError("attachment contains no points")
    for point in attachment.points:
        if not 0 <= point.source_index < source_vertex_count:
            raise AttachmentError("source vertex index is out of range")
        if len(point.target_triangle) != 3 or any(
                index < 0 or index >= target_vertex_count
                for index in point.target_triangle):
            raise AttachmentError("target triangle index is out of range")
        if len(point.target_weights) != 3 or any(
                not math.isfinite(value) for value in point.target_weights):
            raise AttachmentError("target barycentric weights are invalid")
        if abs(sum(point.target_weights) - 1.0) > 1.0e-4:
            raise AttachmentError("target barycentric weights must sum to one")


def wire_entry(attachment: ObjectAttachment, *,
               source_vertex_count: int, target_vertex_count: int,
               position_transform=lambda value: value) -> dict:
    """Return the canonical upstream ``cross_stitch`` group entry."""
    validate_attachment(
        attachment, source_vertex_count=source_vertex_count,
        target_vertex_count=target_vertex_count)
    return {
        "source_uuid": attachment.source_uuid,
        "target_uuid": attachment.target_uuid,
        "ind": [[point.source_index, point.source_index, point.source_index,
                 *point.target_triangle] for point in attachment.points],
        "w": [[1.0, 0.0, 0.0, *map(float, point.target_weights)]
              for point in attachment.points],
        "source_points": [list(map(float, position_transform(point.source_point)))
                          for point in attachment.points],
        "target_points": [list(map(float, position_transform(point.target_point)))
                          for point in attachment.points],
        "stitch_stiffness": float(attachment.stiffness),
    }
