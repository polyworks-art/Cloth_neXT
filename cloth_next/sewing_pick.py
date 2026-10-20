# SPDX-License-Identifier: GPL-3.0-or-later
"""Vectorised screen-space picking and edge routing for viewport authoring.

Blender-independent so that dense-mesh behaviour can be unit tested. The
Blender adapter supplies the view matrix, region size and visibility test.
"""
from __future__ import annotations

import heapq
import math

import numpy as np

PICK_RADIUS = 12.0


def project(perspective_matrix, positions, width, height):
    """Project world positions to region pixels; NaN rows lie behind the view.

    Matches ``bpy_extras.view3d_utils.location_3d_to_region_2d``.
    """
    positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    matrix = np.asarray(perspective_matrix, dtype=np.float64).reshape(4, 4)
    homogeneous = np.c_[positions, np.ones(len(positions))]
    clip = homogeneous @ matrix.T
    w = clip[:, 3]
    result = np.full((len(positions), 2), np.nan)
    front = w > 1e-9
    result[front, 0] = width * .5 * (1.0 + clip[front, 0] / w[front])
    result[front, 1] = height * .5 * (1.0 + clip[front, 1] / w[front])
    return result


class EdgeProjection:
    """Projected edge segments for one object in one view."""

    def __init__(self, projected, edges):
        edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
        self.edges = edges
        if not len(edges):
            self.a = self.d = np.zeros((0, 2))
            self.length2 = np.zeros(0)
            return
        a, b = projected[edges[:, 0]], projected[edges[:, 1]]
        valid = ~(np.isnan(a).any(axis=1) | np.isnan(b).any(axis=1))
        self.edges = edges[valid]
        self.a = a[valid]
        self.d = b[valid] - self.a
        self.length2 = np.maximum((self.d * self.d).sum(axis=1), 1e-12)

    def nearest(self, mouse, radius=PICK_RADIUS, limit=8):
        """Return up to ``limit`` (distance², edge, t) candidates, nearest first."""
        if not len(self.edges):
            return ()
        m = np.asarray(mouse, dtype=np.float64)
        t = np.clip(((m - self.a) * self.d).sum(axis=1) / self.length2, 0.0, 1.0)
        q = self.a + self.d * t[:, None]
        distance = ((q - m) ** 2).sum(axis=1)
        hits = np.nonzero(distance <= radius * radius)[0]
        if not len(hits):
            return ()
        order = hits[np.lexsort((hits, distance[hits]))][:limit]
        return tuple((float(distance[i]), (int(self.edges[i, 0]), int(self.edges[i, 1])),
                      float(t[i])) for i in order)


def nearest_vertices(projected, indices, mouse, radius=PICK_RADIUS, limit=8):
    """Nearest projected vertices among ``indices`` (all when None)."""
    if indices is None:
        indices = np.arange(len(projected))
    else:
        indices = np.asarray(sorted(indices), dtype=np.int64)
    if not len(indices):
        return ()
    points = projected[indices]
    distance = ((points - np.asarray(mouse, dtype=np.float64)) ** 2).sum(axis=1)
    distance[np.isnan(distance)] = np.inf
    hits = np.nonzero(distance <= radius * radius)[0]
    order = hits[np.lexsort((indices[hits], distance[hits]))][:limit]
    return tuple((float(distance[i]), int(indices[i])) for i in order)


def boundary_edges(polygons):
    """Edges used by exactly one polygon, as sorted vertex pairs."""
    counts = {}
    for polygon in polygons:
        polygon = tuple(map(int, polygon))
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            key = (a, b) if a < b else (b, a)
            counts[key] = counts.get(key, 0) + 1
    return tuple(sorted(edge for edge, count in counts.items() if count == 1))


def adjacency(vertex_count, edges):
    result = [[] for _ in range(vertex_count)]
    for a, b in edges:
        a, b = int(a), int(b)
        result[a].append(b)
        result[b].append(a)
    return result


def route(vertices, neighbours, start, end, *, forbidden=frozenset(), limit=200_000):
    """A* shortest edge route that never visits ``forbidden`` vertices.

    Used only to preview and extend Free Sewing between two explicit points;
    every explicit point stays on the path.
    """
    start, end = int(start), int(end)
    if start == end:
        return (start,)
    goal = vertices[end]

    def heuristic(index):
        return math.dist(vertices[index], goal)

    best = {start: 0.0}
    previous = {}
    queue = [(heuristic(start), 0.0, start)]
    visited = 0
    while queue:
        _, cost, current = heapq.heappop(queue)
        if cost > best.get(current, math.inf):
            continue
        if current == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return tuple(reversed(path))
        visited += 1
        if visited > limit:
            break
        here = vertices[current]
        for neighbour in neighbours[current]:
            if neighbour in forbidden and neighbour != end:
                continue
            candidate = cost + math.dist(here, vertices[neighbour])
            if candidate < best.get(neighbour, math.inf) - 1e-15:
                best[neighbour] = candidate
                previous[neighbour] = current
                heapq.heappush(queue, (candidate + heuristic(neighbour), candidate, neighbour))
    return None


def polyline_distance(points, mouse):
    """Smallest pixel distance from ``mouse`` to a projected polyline."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    if len(points) < 2:
        return math.inf
    a, b = points[:-1], points[1:]
    valid = ~(np.isnan(a).any(axis=1) | np.isnan(b).any(axis=1))
    if not valid.any():
        return math.inf
    a, d = a[valid], b[valid] - a[valid]
    m = np.asarray(mouse, dtype=np.float64)
    t = np.clip(((m - a) * d).sum(axis=1) / np.maximum((d * d).sum(axis=1), 1e-12), 0, 1)
    q = a + d * t[:, None]
    return float(np.sqrt(((q - m) ** 2).sum(axis=1).min()))
