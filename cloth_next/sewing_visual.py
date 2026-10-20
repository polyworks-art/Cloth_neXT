# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolution independent authoring geometry; never used to generate solver constraints."""
from bisect import bisect_right
import colorsys
import hashlib
import math


def seam_color(identifier):
    hue = int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:4], 'big') / 2**32
    return colorsys.hsv_to_rgb(hue, .72, 1.0)


def length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def sample(points, count):
    if len(points) < 2:
        return ()
    arc = [0.0]
    for a, b in zip(points, points[1:]):
        arc.append(arc[-1] + math.dist(a, b))
    if arc[-1] <= 1e-12:
        return ()
    result = []
    for i in range(count):
        distance = arc[-1] * i / (count - 1)
        j = min(bisect_right(arc, distance) - 1, len(points) - 2)
        width = arc[j + 1] - arc[j]
        t = (distance - arc[j]) / width if width else 0
        result.append(tuple(a + (b - a) * t for a, b in zip(points[j], points[j + 1])))
    return tuple(result)


def threads(first, second, count, flipped=False):
    a, b = sample(first, count), sample(second[::-1] if flipped else second, count)
    return tuple(p for pair in zip(a, b) for p in pair)


def thread_count(projected_length, density=1.0):
    return max(2, min(1024, round(projected_length * density / 9) + 1))


def segment(vertices, edges, seed, corner_degrees=38, *, neighbours=None):
    """Walk a boundary until a corner, branch or closed loop; no interior shortcut.

    ``neighbours`` may be a prebuilt boundary adjacency (list or mapping) so a
    hover on a dense garment does not rebuild it for every mouse movement.
    """
    if neighbours is None:
        neighbours = {}
        for a, b in edges:
            neighbours.setdefault(a, []).append(b)
            neighbours.setdefault(b, []).append(a)
    get = neighbours.get if isinstance(neighbours, dict) else (
        lambda index, default=(): neighbours[index] if 0 <= index < len(neighbours) else default)
    cosine = math.cos(math.radians(corner_degrees))
    path = list(seed)
    members = set(path)
    for reverse in (False, True):
        if reverse:
            path.reverse()
        while len(get(path[-1], ())) == 2:
            previous, current = path[-2:]
            nxt = next(v for v in get(current, ()) if v != previous)
            if nxt in members:
                break
            u = tuple(b - a for a, b in zip(vertices[previous], vertices[current]))
            v = tuple(b - a for a, b in zip(vertices[current], vertices[nxt]))
            norm = math.sqrt(sum(x*x for x in u) * sum(x*x for x in v))
            if norm <= 1e-12 or sum(a*b for a, b in zip(u, v)) / norm < cosine:
                break
            path.append(nxt)
            members.add(nxt)
    return tuple(reversed(path))


def validate_paths(vertices_a, edges_a, a, vertices_b, edges_b, b, same=False):
    for vertices, edges, path in ((vertices_a, edges_a, a), (vertices_b, edges_b, b)):
        if len(path) < 2 or len(set(path)) != len(path):
            raise ValueError('Each side needs a connected path without repeated vertices')
        allowed = (edges if isinstance(edges, (set, frozenset))
                   else {tuple(sorted(e)) for e in edges})
        if any(i < 0 or i >= len(vertices) for i in path):
            raise ValueError('Topology changed; select the side again')
        if any(tuple(sorted(e)) not in allowed for e in zip(path, path[1:])):
            raise ValueError('Path contains disconnected edges')
        if length([vertices[i] for i in path]) <= 1e-12:
            raise ValueError('Side has zero length')
    if same:
        # Darts and adjacent panel edges legitimately meet in one corner.
        # Only an endpoint may be shared; interior overlap would sew a path
        # onto itself.
        if tuple(a) == tuple(b) or tuple(a) == tuple(reversed(b)):
            raise ValueError('Both sides are the same path')
        shared = set(a) & set(b)
        endpoints = {a[0], a[-1]} & {b[0], b[-1]}
        if shared - endpoints:
            raise ValueError('Sides overlap; select distinct sewing boundaries')
