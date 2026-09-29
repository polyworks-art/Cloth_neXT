# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure data model and validation for cross-object solver attachments."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math


SUPPORTED_ROLES = frozenset({"CLOTH", "SOFT_BODY"})
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
    if attachment.target_role not in SUPPORTED_ROLES:
        raise AttachmentError("target must be Cloth or Soft Body")
    if not attachment.source_uuid or not attachment.target_uuid:
        raise AttachmentError("source and target UUIDs are required")
    if attachment.source_uuid == attachment.target_uuid:
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
