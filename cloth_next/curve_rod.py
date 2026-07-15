# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic Blender Curve to PPF rod control-point bridge."""

from __future__ import annotations

import math


class CurveRodError(ValueError):
    pass


def sample_curve(obj):
    vertices, edges, splines = [], [], []
    for spline in obj.data.splines:
        if spline.type == "BEZIER":
            points = spline.bezier_points
        elif spline.type == "POLY":
            points = spline.points
        else:
            raise CurveRodError(
                f"{obj.name}: {spline.type} splines are not supported for Rod; "
                "convert the spline to Bezier or Poly.")
        if len(points) < 2:
            raise CurveRodError(f"{obj.name}: every Rod spline needs 2+ points.")
        base = len(vertices)
        for point in points:
            co = point.co
            position = (float(co[0]), float(co[1]), float(co[2]))
            if any(not math.isfinite(value) for value in position):
                raise CurveRodError(f"{obj.name}: non-finite Curve point.")
            vertices.append(position)
        for index in range(len(points) - 1):
            edges.append((base + index, base + index + 1))
        if spline.use_cyclic_u:
            edges.append((base + len(points) - 1, base))
        splines.append((spline.type, len(points), bool(spline.use_cyclic_u)))
    if not vertices or not edges:
        raise CurveRodError(f"{obj.name}: Curve has no usable Rod segments.")
    return tuple(vertices), tuple(edges), tuple(splines)
