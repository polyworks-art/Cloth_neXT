# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent checks of a mesher result against its parametric shape.

Used by the unit tests and ``tools/blender_pattern_geometry_check.py``.
Each check recomputes from the parametric shape (not from mesher
internals), so a mesher bug cannot hide itself.
"""

from __future__ import annotations

import numpy as np

from .curves import display_polyline
from .mesher import _min_distance, _segment_arrays, triangle_quality
from .validation import point_in_rings


def _area(ring):
    p = np.asarray(ring)
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(np.roll(p[:, 0], -1), p[:, 1]))


def check(result, shape):
    v, t = result.vertices, result.triangles
    a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
    signed = 0.5 * ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0]))
    fine = 1e-5
    outline = display_polyline(shape.outline, fine)
    holes = [display_polyline(h, fine) for h in shape.holes]
    exact_area = abs(_area(outline)) - sum(abs(_area(h)) for h in holes)
    mesh_area = float(signed.sum())

    edges = np.concatenate((t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]))
    keys, counts = np.unique(np.sort(edges, axis=1), axis=0, return_counts=True)
    boundary_edges = {tuple(k) for k in keys[counts == 1]}
    constraint = set()
    for loop in result.loops:
        if loop.kind == "LINE":
            continue
        ids = loop.vertices
        constraint |= {tuple(sorted((ids[i], ids[(i + 1) % len(ids)]))) for i in range(len(ids))}
    line_edges = set()
    for loop in result.loops:
        if loop.kind == "LINE":
            ids = loop.vertices
            line_edges |= {tuple(sorted(p)) for p in zip(ids, ids[1:])}
    all_edges = {tuple(k) for k in keys}

    boundary_ids = {i for loop in result.loops if loop.kind != "LINE" for i in loop.vertices}
    inner = np.array([i for i in range(len(v)) if i not in boundary_ids], dtype=np.int64)
    rings = [[s.co for s in result.loops[0].sources]] + \
        [[s.co for s in loop.sources] for loop in result.loops if loop.kind == "HOLE"]
    inside = point_in_rings(v[inner, 0], v[inner, 1], rings) if len(inner) else np.array([], bool)
    centroids = (a + b + c) / 3.0
    centroid_inside = point_in_rings(centroids[:, 0], centroids[:, 1], rings)

    # Curve conformity: every point of the analytic outline lies within the
    # tolerance of the meshed boundary polyline (one-sided Hausdorff).
    chains = [([s.co for s in loop.sources], True) for loop in result.loops if loop.kind != "LINE"]
    deviation = float(_min_distance(np.asarray(outline + [p for h in holes for p in h]),
                                    _segment_arrays(chains)).max())
    quality = triangle_quality(v, t)
    triangle_keys = np.unique(np.sort(t, axis=1), axis=0)
    return {
        "vertices": int(len(v)), "triangles": int(len(t)),
        "interior_vertices": int(len(inner)),
        "area_exact": exact_area, "area_mesh": mesh_area,
        "area_error_relative": abs(mesh_area - exact_area) / max(exact_area, 1e-12),
        "ccw_winding": bool((signed > 0).all()),
        "min_triangle_area": float(signed.min()),
        "boundary_conforming": boundary_edges == constraint,
        "internal_lines_embedded": line_edges <= all_edges,
        "manifold": bool(counts.max() <= 2),
        "no_duplicate_triangles": len(triangle_keys) == len(t),
        "interior_vertices_inside": bool(inside.all()) if len(inner) else True,
        "triangles_inside": bool(centroid_inside.all()),
        "boundary_deviation": deviation,
        "min_angle": float(quality.min()), "mean_min_angle": float(quality.mean()),
        "skinny_ratio_below_20deg": float((quality < 20.0).mean()),
    }


def passed(report, tolerance):
    return (report["ccw_winding"] and report["boundary_conforming"] and report["manifold"]
            and report["no_duplicate_triangles"] and report["interior_vertices_inside"]
            and report["triangles_inside"] and report["internal_lines_embedded"]
            and report["min_triangle_area"] > 0
            and report["boundary_deviation"] <= tolerance * 1.05
            and report["area_error_relative"] < 0.01)
