# SPDX-License-Identifier: GPL-3.0-or-later
"""Small pure-Python Delaunay triangulation for headless tests.

The add-on always meshes with Blender's exact constrained Delaunay
(``mathutils.geometry.delaunay_2d_cdt``). This Bowyer-Watson fallback lets
the mesher run under plain pytest; it recovers missing constraint edges by
flipping and raises when it cannot, so a test never passes on a mesh that
ignores the boundary.
"""

from __future__ import annotations

import numpy as np


def _circumcircle(p, a, b, c):
    ax, ay = p[a]
    bx, by = p[b]
    cx, cy = p[c]
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-30:
        return (0.0, 0.0, float("inf"))
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy, (ax - ux) ** 2 + (ay - uy) ** 2)


def _orient(p, a, b, c):
    return ((p[b][0] - p[a][0]) * (p[c][1] - p[a][1])
            - (p[b][1] - p[a][1]) * (p[c][0] - p[a][0]))


def delaunay(points):
    pts = [tuple(map(float, q)) for q in np.asarray(points)]
    n = len(pts)
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-9) * 50.0
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    pts += [(cx - span, cy - span), (cx + span, cy - span), (cx, cy + span)]
    triangles = {(n, n + 1, n + 2): _circumcircle(pts, n, n + 1, n + 2)}
    # Insert in a spatially coherent order to keep the cavity search cheap.
    order = sorted(range(n), key=lambda i: (round(pts[i][1] * 1e6), pts[i][0]))
    for i in order:
        x, y = pts[i]
        bad = [t for t, (ux, uy, r2) in triangles.items()
               if (x - ux) ** 2 + (y - uy) ** 2 < r2 * (1 + 1e-12)]
        boundary = {}
        for t in bad:
            for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
                key = tuple(sorted(e))
                if key in boundary:
                    del boundary[key]
                else:
                    boundary[key] = e
            del triangles[t]
        for a, b in boundary.values():
            tri = (a, b, i) if _orient(pts, a, b, i) > 0 else (b, a, i)
            triangles[tri] = _circumcircle(pts, *tri)
    return pts, [t for t in triangles if max(t) < n]


def _segments_cross(p, a, b, c, d):
    return (_orient(p, a, b, c) * _orient(p, a, b, d) < 0
            and _orient(p, c, d, a) * _orient(p, c, d, b) < 0)


def constrained(points, edges, outline=None, max_flips=100000):
    pts, triangles = delaunay(points)
    triangles = [tuple(t) for t in triangles]
    for a, b in edges:
        flips = 0
        while True:
            edge_set = {tuple(sorted(e)) for t in triangles
                        for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0]))}
            if tuple(sorted((a, b))) in edge_set:
                break
            # Find a triangle edge crossing (a, b) and flip it.
            crossing = None
            for u, v in edge_set:
                if a in (u, v) or b in (u, v):
                    continue
                if _segments_cross(pts, a, b, u, v):
                    crossing = (u, v)
                    break
            if crossing is None or flips > max_flips:
                raise RuntimeError(f"fallback CDT could not recover edge {a}-{b}")
            u, v = crossing
            pair = [t for t in triangles if u in t and v in t]
            if len(pair) != 2:
                raise RuntimeError("fallback CDT: non-manifold flip")
            w1 = next(k for k in pair[0] if k not in (u, v))
            w2 = next(k for k in pair[1] if k not in (u, v))
            # Only flip convex quads; otherwise try the next crossing later.
            if _orient(pts, w1, w2, u) * _orient(pts, w1, w2, v) >= 0:
                raise RuntimeError(f"fallback CDT could not recover edge {a}-{b}")
            for t in pair:
                triangles.remove(t)
            for t in ((w1, w2, u), (w1, w2, v)):
                triangles.append(t if _orient(pts, *t) > 0 else (t[0], t[2], t[1]))
            flips += 1
    return np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
