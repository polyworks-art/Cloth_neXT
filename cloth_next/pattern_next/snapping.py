# SPDX-License-Identifier: GPL-3.0-or-later
"""Canvas snapping: pattern vertices, pattern edges, grid and angles.

Priority follows drafting tools: an existing vertex wins over an edge, an
edge over the grid. The radius is given in canvas metres (the editor
converts its pixel radius with the current zoom).
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from .editing import angle_constrain
from .shapes import distance


@dataclass(frozen=True)
class Snap:
    co: tuple
    kind: str = "NONE"  # VERTEX, EDGE, GRID, ANGLE, NONE
    target: object = None


def _foot(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    dd = dx * dx + dy * dy
    t = 0.0 if dd < 1e-30 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / dd))
    return (a[0] + dx * t, a[1] + dy * t)


def snap(co, *, vertices=(), polylines=(), radius=0.0, grid=0.0, origin=None,
         constrain=False, use_vertices=True, use_edges=True, use_grid=False):
    """Return the snapped canvas position for ``co``.

    ``vertices``: iterable of (co, target); ``polylines``: iterable of
    (points, closed, target) in canvas space; ``origin``: previous point for
    angle constraints (Shift).
    """
    if constrain and origin is not None:
        constrained = angle_constrain(origin, co)
        if use_grid and grid > 0:
            # Keep the constrained direction; round the length to the grid.
            d = distance(origin, constrained)
            if d > 0:
                rounded = max(grid, round(d / grid) * grid)
                constrained = (origin[0] + (constrained[0] - origin[0]) * rounded / d,
                               origin[1] + (constrained[1] - origin[1]) * rounded / d)
        return Snap(constrained, "ANGLE")
    if use_vertices and radius > 0:
        best = None
        for point, target in vertices:
            d = distance(point, co)
            if d <= radius and (best is None or d < best[0]):
                best = (d, point, target)
        if best is not None:
            return Snap(tuple(best[1]), "VERTEX", best[2])
    if use_edges and radius > 0:
        best = None
        for points, closed, target in polylines:
            n = len(points)
            pairs = range(n if closed else n - 1)
            for i in pairs:
                foot = _foot(points[i], points[(i + 1) % n], co)
                d = distance(foot, co)
                if d <= radius and (best is None or d < best[0]):
                    best = (d, foot, target)
        if best is not None:
            return Snap(best[1], "EDGE", best[2])
    if use_grid and grid > 0:
        return Snap((round(co[0] / grid) * grid, round(co[1] / grid) * grid), "GRID")
    return Snap(tuple(co))


def nice_grid_step(world_per_pixel, minimum_pixels=12.0):
    """Grid step (1, 2, 5 x 10^n metres) at least ``minimum_pixels`` apart."""
    target = max(world_per_pixel * minimum_pixels, 1e-6)
    exponent = math.floor(math.log10(target))
    for factor in (1, 2, 5, 10):
        step = factor * 10 ** exponent
        if step >= target:
            return step
    return 10 ** (exponent + 1)
