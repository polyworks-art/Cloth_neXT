# SPDX-License-Identifier: GPL-3.0-or-later
"""Bezier flattening and Particle-Distance boundary resampling.

Meshing tolerance (documented in ``docs/PATTERN_NEXT.md``): every straight
mesh edge on a curved boundary stays within ``tolerance`` (default 1 mm) of
the analytic Bezier curve. Segments are first sampled at equal arc length
with roughly ``spacing`` (Particle Distance) between vertices; a segment is
then refined until the chord deviation bound holds, so tight curves get
shorter boundary edges than the interior spacing.
"""

from __future__ import annotations

from dataclasses import dataclass
import bisect
import math

from .shapes import Contour, distance, lerp

MAX_SAMPLES_PER_SEGMENT = 20000


@dataclass(frozen=True)
class BoundarySample:
    co: tuple
    segment_uid: int     # uid of the contour point that starts the segment
    fraction: float      # arc-length fraction along that segment, [0, 1)
    corner: bool         # True exactly at a contour point (fraction 0)


def bezier(p0, c0, c1, p1, t):
    u = 1.0 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * c0[0] + c * c1[0] + d * p1[0],
            a * p0[1] + b * c0[1] + c * c1[1] + d * p1[1])


def _point_line_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    denominator = dx * dx + dy * dy
    if denominator < 1e-30:
        return distance(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / denominator))
    return distance(p, (a[0] + dx * t, a[1] + dy * t))


def flatten(p0, c0, c1, p1, curved, tolerance, depth=0):
    """Dense polyline from ``p0`` to ``p1`` (both included)."""
    if not curved:
        return [p0, p1]
    flatness = max(_point_line_distance(c0, p0, p1), _point_line_distance(c1, p0, p1))
    if flatness <= tolerance or depth >= 18:
        return [p0, p1]
    # de Casteljau split at t = 0.5
    p01, p12, p23 = lerp(p0, c0, .5), lerp(c0, c1, .5), lerp(c1, p1, .5)
    p012, p123 = lerp(p01, p12, .5), lerp(p12, p23, .5)
    mid = lerp(p012, p123, .5)
    left = flatten(p0, p01, p012, mid, True, tolerance, depth + 1)
    right = flatten(mid, p123, p23, p1, True, tolerance, depth + 1)
    return left[:-1] + right


def segment_polyline(contour: Contour, index, tolerance):
    p0, c0, c1, p1, curved = contour.segment(index)
    return flatten(p0, c0, c1, p1, curved, tolerance * 0.25)


def display_polyline(contour: Contour, tolerance=0.0005):
    """Closed (or open) dense outline for drawing and hit testing."""
    result = []
    for index in range(contour.segment_count):
        result.extend(segment_polyline(contour, index, tolerance)[:-1])
    if not contour.closed and contour.points:
        result.append(contour.points[-1].co)
    return result


def _cumulative(points):
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + distance(a, b))
    return lengths


def _at(points, lengths, s):
    i = min(max(bisect.bisect_right(lengths, s) - 1, 0), len(points) - 2)
    span = lengths[i + 1] - lengths[i]
    t = 0.0 if span <= 0.0 else (s - lengths[i]) / span
    return lerp(points[i], points[i + 1], t), i


def segment_length(contour: Contour, index, tolerance=0.0005):
    return _cumulative(segment_polyline(contour, index, tolerance))[-1]


def _resample_segment(dense, spacing, tolerance, curved):
    lengths = _cumulative(dense)
    total = lengths[-1]
    if total <= 0.0:
        return [(dense[0], 0.0)]
    count = max(1, int(math.ceil(total / spacing - 1e-9)))
    while True:
        stations = [total * k / count for k in range(count)]
        samples = [_at(dense, lengths, s) for s in stations]
        if not curved or count >= MAX_SAMPLES_PER_SEGMENT:
            break
        ok = True
        ends = samples[1:] + [(dense[-1], len(dense) - 2)]
        for (a, ia), (b, ib) in zip(samples, ends):
            for j in range(ia + 1, min(ib, len(dense) - 1) + 1):
                if _point_line_distance(dense[j], a, b) > tolerance:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            break
        count = min(MAX_SAMPLES_PER_SEGMENT, int(count * 1.3) + 1)
    return [(point, s / total) for (point, _), s in zip(samples, stations)]


def resample(contour: Contour, spacing, tolerance):
    """Boundary vertices at ~``spacing`` with stable segment sources.

    Closed contours do not repeat the first sample; open contours end with
    their last point (``segment_uid`` of the final point, fraction 0).
    """
    if spacing <= 0:
        raise ValueError("Particle Distance must be positive")
    result = []
    for index in range(contour.segment_count):
        p0, c0, c1, p1, curved = contour.segment(index)
        dense = flatten(p0, c0, c1, p1, curved, tolerance * 0.25)
        uid = contour.points[index].uid
        for co, fraction in _resample_segment(dense, spacing, tolerance, curved):
            result.append(BoundarySample(co, uid, fraction, fraction == 0.0))
    if not contour.closed and contour.points:
        last = contour.points[-1]
        result.append(BoundarySample(last.co, last.uid, 0.0, True))
    return result
