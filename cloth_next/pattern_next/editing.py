# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure contour edits used by the PATTERN NeXt tools.

Every function returns a new tuple of :class:`ContourPoint`; nothing here
knows about Blender. Inserting a point into a Bezier segment uses an exact
de Casteljau split, so splitting never changes the curve's shape.
"""

from __future__ import annotations

from dataclasses import replace
import math

from .curves import bezier, display_polyline, segment_polyline
from .shapes import (Contour, add, distance, length, lerp, scale, sub)


def _split_bezier(p0, c0, c1, p1, t):
    p01, p12, p23 = lerp(p0, c0, t), lerp(c0, c1, t), lerp(c1, p1, t)
    p012, p123 = lerp(p01, p12, t), lerp(p12, p23, t)
    mid = lerp(p012, p123, t)
    return (p01, p012, mid, p123, p23)


def insert_point(points, index, t, uid):
    """Insert a point on segment ``index`` at curve parameter ``t``."""
    n = len(points)
    a, b = points[index % n], points[(index + 1) % n]
    t = min(max(float(t), 1e-4), 1 - 1e-4)
    result = list(points)
    if a.curve_out:
        p01, p012, mid, p123, p23 = _split_bezier(
            a.co, add(a.co, a.handle_out), add(b.co, b.handle_in), b.co, t)
        new = type(a)(uid, mid, sub(p012, mid), sub(p123, mid), True, True)
        result[index % n] = replace(a, handle_out=sub(p01, a.co))
        result[(index + 1) % n] = replace(b, handle_in=sub(p23, b.co))
    else:
        new = type(a)(uid, lerp(a.co, b.co, t))
    result.insert(index % n + 1, new)
    return tuple(result)


def remove_point(points, index):
    if len(points) <= 3:
        raise ValueError("A pattern needs at least 3 points")
    result = list(points)
    del result[index % len(points)]
    return tuple(result)


def move_point(points, index, co):
    result = list(points)
    result[index] = replace(result[index], co=tuple(co))
    return tuple(result)


def move_points(points, indices, delta):
    result = list(points)
    for i in set(indices):
        result[i % len(points)] = replace(result[i % len(points)],
                                          co=add(result[i % len(points)].co, delta))
    return tuple(result)


def set_handle(points, index, side, offset, symmetric=None):
    """Move handle ``side`` ("in"/"out") of point ``index``.

    Smooth points keep the opposite handle collinear (its own length kept);
    ``symmetric`` overrides the point's smooth flag.
    """
    p = points[index]
    offset = tuple(offset)
    other_name = "handle_in" if side == "out" else "handle_out"
    own_name = "handle_out" if side == "out" else "handle_in"
    changes = {own_name: offset}
    if p.smooth if symmetric is None else symmetric:
        other = getattr(p, other_name)
        size = length(other) or length(offset)
        current = length(offset)
        if current > 1e-12:
            changes[other_name] = scale(offset, -size / current)
    result = list(points)
    result[index] = replace(p, **changes)
    return tuple(result)


def make_curved(points, index, bulge=0.0):
    """Straight segment -> Bezier with handles at thirds (shape unchanged)."""
    n = len(points)
    a, b = points[index % n], points[(index + 1) % n]
    if a.curve_out:
        return tuple(points)
    chord = sub(b.co, a.co)
    normal = (-chord[1] * bulge, chord[0] * bulge)
    result = list(points)
    result[index % n] = replace(a, curve_out=True, handle_out=add(scale(chord, 1 / 3), normal))
    result[(index + 1) % n] = replace(b, handle_in=add(scale(chord, -1 / 3), normal))
    return tuple(result)


def make_straight(points, index):
    n = len(points)
    a, b = points[index % n], points[(index + 1) % n]
    result = list(points)
    result[index % n] = replace(a, curve_out=False, handle_out=(0.0, 0.0))
    result[(index + 1) % n] = replace(b, handle_in=(0.0, 0.0))
    return tuple(result)


def bend_segment(points, index, grab_t, target):
    """Edit Curve: drag the curve point at ``grab_t`` to ``target``.

    Both inner control points move by the same offset, scaled so the
    grabbed point lands exactly on ``target`` (the Bezier weight of the
    inner controls at t is 3t(1-t)).
    """
    points = make_curved(points, index)
    n = len(points)
    a, b = points[index % n], points[(index + 1) % n]
    c0, c1 = add(a.co, a.handle_out), add(b.co, b.handle_in)
    current = bezier(a.co, c0, c1, b.co, grab_t)
    weight = 3.0 * grab_t * (1.0 - grab_t)
    if weight < 1e-6:
        return points
    delta = scale(sub(target, current), 1.0 / weight)
    result = list(points)
    result[index % n] = replace(a, handle_out=add(a.handle_out, delta))
    result[(index + 1) % n] = replace(b, handle_in=add(b.handle_in, delta))
    return tuple(result)


def set_segment_length(points, index, new_length):
    """Move the segment's end point along the chord to reach ``new_length``.

    Applies to straight segments; curved segments scale their chord.
    """
    n = len(points)
    a, b = points[index % n], points[(index + 1) % n]
    chord = sub(b.co, a.co)
    current = length(chord)
    if current < 1e-12 or new_length <= 0:
        raise ValueError("Cannot set the length of a zero-length edge")
    factor = new_length / current
    if a.curve_out:
        from .curves import segment_length
        arc = segment_length(Contour(tuple(points)), index)
        factor = new_length / max(arc, 1e-12)
    end = add(a.co, scale(chord, factor))
    result = list(points)
    result[(index + 1) % n] = replace(b, co=end)
    if a.curve_out:
        result[index % n] = replace(a, handle_out=scale(a.handle_out, factor))
        result[(index + 1) % n] = replace(result[(index + 1) % n],
                                          handle_in=scale(b.handle_in, factor))
    return tuple(result)


# -- picking ---------------------------------------------------------------

def nearest_point(points, co, radius):
    best = None
    for i, p in enumerate(points):
        d = distance(p.co, co)
        if d <= radius and (best is None or d < best[0]):
            best = (d, i)
    return best


def nearest_handle(points, co, radius, indices=None):
    best = None
    for i in (range(len(points)) if indices is None else indices):
        p = points[i]
        n = len(points)
        for side, offset, active in (("in", p.handle_in, points[(i - 1) % n].curve_out),
                                     ("out", p.handle_out, p.curve_out)):
            if not active:
                continue
            d = distance(add(p.co, offset), co)
            if d <= radius and (best is None or d < best[0]):
                best = (d, i, side)
    return best


def nearest_segment(points, co, radius, closed=True, samples=48):
    """(distance, segment index, bezier t, point on curve) or None."""
    contour = Contour(tuple(points), closed)
    best = None
    for index in range(contour.segment_count):
        p0, c0, c1, p1, curved = contour.segment(index)
        if curved:
            params = [k / samples for k in range(samples + 1)]
            pts = [bezier(p0, c0, c1, p1, t) for t in params]
        else:
            params, pts = [0.0, 1.0], [p0, p1]
        for k in range(len(pts) - 1):
            a, b = pts[k], pts[k + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            dd = dx * dx + dy * dy
            s = 0.0 if dd < 1e-30 else max(0.0, min(1.0, ((co[0] - a[0]) * dx + (co[1] - a[1]) * dy) / dd))
            foot = (a[0] + dx * s, a[1] + dy * s)
            d = distance(foot, co)
            if d <= radius and (best is None or d < best[0]):
                t = params[k] + (params[k + 1] - params[k]) * s
                best = (d, index, t, foot)
    return best


def ring_contains(rings, co):
    """Even-odd point-in-polygon over flattened rings."""
    x, y = co
    inside = False
    for ring in rings:
        n = len(ring)
        for i in range(n):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % n]
            if (ay > y) != (by > y):
                if x < ax + (y - ay) * (bx - ax) / (by - ay):
                    inside = not inside
    return inside


def angle_constrain(origin, co, step=math.radians(45.0)):
    """Snap the direction origin->co to multiples of ``step``."""
    v = sub(co, origin)
    size = length(v)
    if size < 1e-12:
        return co
    angle = round(math.atan2(v[1], v[0]) / step) * step
    return add(origin, (size * math.cos(angle), size * math.sin(angle)))


def polyline(points, closed=True, tolerance=0.0005):
    return display_polyline(Contour(tuple(points), closed), tolerance)


def segment_points(points, index, closed=True, tolerance=0.0005):
    return segment_polyline(Contour(tuple(points), closed), index, tolerance)
