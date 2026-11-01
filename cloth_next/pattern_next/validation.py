# SPDX-License-Identifier: GPL-3.0-or-later
"""Contour validation: nothing invalid is ever meshed.

Every check works on the flattened polyline (curves included), so a Bezier
that loops over itself is rejected exactly like a crossing straight edge.
"""

from __future__ import annotations

import numpy as np

from .shapes import signed_area

MIN_EDGE = 1e-4      # 0.1 mm: shorter edges count as duplicate points
MIN_AREA = 1e-6      # 1 mm^2


class PatternError(ValueError):
    """A contour that must not be meshed; ``code`` is stable for tests/UI."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _segments(points, closed=True):
    array = np.asarray(points, dtype=np.float64)
    if closed:
        return np.stack((array, np.roll(array, -1, axis=0)), axis=1)
    return np.stack((array[:-1], array[1:]), axis=1)


def _orient(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def crossing_pairs(first, second=None, *, closed_first=True, closed_second=True,
                   limit=1, eps=1e-12):
    """Index pairs of intersecting segments (touching counts).

    With ``second`` omitted, adjacent segments of the same ring are skipped.
    Rows are processed in chunks so thousands of segments stay cheap.
    """
    same = second is None
    a = _segments(first, closed_first)
    b = a if same else _segments(second, closed_second)
    n, m = len(a), len(b)
    found = []
    if n == 0 or m == 0:
        return found
    bx0, by0, bx1, by1 = b[:, 0, 0], b[:, 0, 1], b[:, 1, 0], b[:, 1, 1]
    bminx, bmaxx = np.minimum(bx0, bx1), np.maximum(bx0, bx1)
    bminy, bmaxy = np.minimum(by0, by1), np.maximum(by0, by1)
    columns = np.arange(m)
    for start in range(0, n, 256):
        rows = a[start:start + 256]
        ax0, ay0 = rows[:, 0, 0:1], rows[:, 0, 1:2]
        ax1, ay1 = rows[:, 1, 0:1], rows[:, 1, 1:2]
        boxes = ((np.minimum(ax0, ax1) <= bmaxx + eps) & (np.maximum(ax0, ax1) >= bminx - eps)
                 & (np.minimum(ay0, ay1) <= bmaxy + eps) & (np.maximum(ay0, ay1) >= bminy - eps))
        if same:
            index = np.arange(start, start + len(rows))[:, None]
            boxes &= columns[None, :] > index + 1
            if closed_first and n > 2:
                boxes &= ~((index == 0) & (columns[None, :] == n - 1))
        if not boxes.any():
            continue
        r, c = np.nonzero(boxes)
        d1 = _orient(ax0[r, 0], ay0[r, 0], ax1[r, 0], ay1[r, 0], bx0[c], by0[c])
        d2 = _orient(ax0[r, 0], ay0[r, 0], ax1[r, 0], ay1[r, 0], bx1[c], by1[c])
        d3 = _orient(bx0[c], by0[c], bx1[c], by1[c], ax0[r, 0], ay0[r, 0])
        d4 = _orient(bx0[c], by0[c], bx1[c], by1[c], ax1[r, 0], ay1[r, 0])
        hit = (((d1 > eps) & (d2 < -eps)) | ((d1 < -eps) & (d2 > eps))) & \
              (((d3 > eps) & (d4 < -eps)) | ((d3 < -eps) & (d4 > eps)))
        # An endpoint lying on the other segment (touching, collinear overlap).
        def on(px, py, qx0, qy0, qx1, qy1, d):
            return ((np.abs(d) <= eps)
                    & (px >= np.minimum(qx0, qx1) - eps) & (px <= np.maximum(qx0, qx1) + eps)
                    & (py >= np.minimum(qy0, qy1) - eps) & (py <= np.maximum(qy0, qy1) + eps))
        rx0, ry0, rx1, ry1 = ax0[r, 0], ay0[r, 0], ax1[r, 0], ay1[r, 0]
        touching = (on(bx0[c], by0[c], rx0, ry0, rx1, ry1, d1)
                    | on(bx1[c], by1[c], rx0, ry0, rx1, ry1, d2)
                    | on(rx0, ry0, bx0[c], by0[c], bx1[c], by1[c], d3)
                    | on(rx1, ry1, bx0[c], by0[c], bx1[c], by1[c], d4))
        for i in np.nonzero(hit | touching)[0]:
            found.append((int(start + r[i]), int(c[i])))
            if len(found) >= limit:
                return found
    return found


def validate_ring(points, *, what="Pattern outline", min_edge=MIN_EDGE, min_area=MIN_AREA):
    """Raise :class:`PatternError` for a closed ring that cannot be meshed."""
    if len(points) < 3:
        raise PatternError("TOO_FEW_POINTS", f"{what} needs at least 3 points")
    array = np.asarray(points, dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise PatternError("NOT_FINITE", f"{what} has an invalid coordinate")
    edges = np.linalg.norm(np.roll(array, -1, axis=0) - array, axis=1)
    short = np.nonzero(edges < min_edge)[0]
    if len(short):
        i = int(short[0])
        code = "DUPLICATE_POINT" if edges[i] < 1e-9 else "ZERO_LENGTH_EDGE"
        raise PatternError(code, f"{what}: point {i + 1} sits on the next point "
                                 f"(edge shorter than {min_edge * 1000:.1f} mm)")
    if abs(signed_area(points)) < min_area:
        raise PatternError("ZERO_AREA", f"{what} encloses almost no area")
    # Adjacent edges are skipped by the crossing test; a 180 degree turn
    # (an edge folding back onto the previous one) is a zero-width spike.
    incoming = array - np.roll(array, 1, axis=0)
    outgoing = np.roll(array, -1, axis=0) - array
    cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    dot = (incoming * outgoing).sum(axis=1)
    norms = np.linalg.norm(incoming, axis=1) * np.linalg.norm(outgoing, axis=1)
    spikes = np.nonzero((dot < 0) & (np.abs(cross) <= 1e-9 * norms))[0]
    if len(spikes):
        raise PatternError("SELF_INTERSECTION",
                           f"{what} folds back on itself at point {int(spikes[0]) + 1}")
    crossing = crossing_pairs(points)
    if crossing:
        raise PatternError("SELF_INTERSECTION", f"{what} crosses itself")


def point_in_rings(x, y, rings):
    """Even-odd inside test for arrays of points against several rings."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    inside = np.zeros(x.shape, dtype=bool)
    for ring in rings:
        r = np.asarray(ring, dtype=np.float64)
        x0, y0 = r[:, 0], r[:, 1]
        x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
        for start in range(0, len(r), 512):
            sl = slice(start, start + 512)
            ax, ay, bx, by = x0[sl], y0[sl], x1[sl], y1[sl]
            straddle = (ay[None, :] > y[..., None]) != (by[None, :] > y[..., None])
            with np.errstate(divide="ignore", invalid="ignore"):
                xs = ax[None, :] + (y[..., None] - ay[None, :]) * (bx - ax)[None, :] / (by - ay)[None, :]
            crossings = np.count_nonzero(straddle & (x[..., None] < xs), axis=-1)
            inside ^= (crossings % 2).astype(bool)
    return inside


def validate_shape(outline, holes=(), lines=()):
    """Validate flattened outline/hole rings and open internal lines."""
    validate_ring(outline)
    for number, hole in enumerate(holes, 1):
        validate_ring(hole, what=f"Hole {number}")
        if crossing_pairs(hole, outline, limit=1):
            raise PatternError("HOLE_INTERSECTS", f"Hole {number} touches the outline")
        h = np.asarray(hole)
        if not point_in_rings(h[:, 0], h[:, 1], [outline]).all():
            raise PatternError("HOLE_OUTSIDE", f"Hole {number} is not inside the pattern")
        for other_number, other in enumerate(holes[:number - 1], 1):
            if crossing_pairs(hole, other, limit=1):
                raise PatternError("HOLE_INTERSECTS",
                                   f"Hole {number} overlaps hole {other_number}")
            o = np.asarray(other)
            if point_in_rings(h[:1, 0], h[:1, 1], [other]).any() or \
                    point_in_rings(o[:1, 0], o[:1, 1], [hole]).any():
                raise PatternError("HOLE_NESTED",
                                   f"Hole {number} is nested in hole {other_number}")
    rings = [outline, *holes]
    for number, line in enumerate(lines, 1):
        if len(line) < 2:
            raise PatternError("TOO_FEW_POINTS", f"Internal line {number} needs 2 points")
        if crossing_pairs(line, None, closed_first=False):
            raise PatternError("SELF_INTERSECTION", f"Internal line {number} crosses itself")
        a = np.asarray(line)
        if not point_in_rings(a[:, 0], a[:, 1], rings).all():
            raise PatternError("LINE_OUTSIDE",
                               f"Internal line {number} leaves the pattern")
        for ring in rings:
            if crossing_pairs(line, ring, closed_first=False, limit=1):
                raise PatternError("LINE_OUTSIDE",
                                   f"Internal line {number} crosses the boundary")
        for other_number, other in enumerate(lines[:number - 1], 1):
            if crossing_pairs(line, other, closed_first=False, closed_second=False, limit=1):
                raise PatternError("LINE_INTERSECTS",
                                   f"Internal line {number} crosses line {other_number}")
