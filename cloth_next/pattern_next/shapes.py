# SPDX-License-Identifier: GPL-3.0-or-later
"""Parametric pattern contours and the 2D canvas transform.

A contour is an ordered ring of :class:`ContourPoint`. The segment that
starts at point ``i`` ends at point ``i + 1`` (wrapping for closed
contours) and is identified by the start point's ``uid``. A segment is a
cubic Bezier when ``curve_out`` is set: its control points are
``co + handle_out`` of the start point and ``co + handle_in`` of the end
point. Handles are offsets, so moving a point carries its handles along.

Point uids are allocated per pattern and never reused, which is what makes
seam references and internal-line anchors survive remeshing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math

Vec = tuple  # (x, y)

# Cubic Bezier circle constant: four quarter arcs with this handle length
# deviate from a true circle by at most 0.027 % of the radius.
KAPPA = 4.0 * (math.sqrt(2.0) - 1.0) / 3.0


@dataclass(frozen=True)
class ContourPoint:
    uid: int
    co: Vec
    handle_in: Vec = (0.0, 0.0)
    handle_out: Vec = (0.0, 0.0)
    curve_out: bool = False  # segment starting here is a cubic Bezier
    smooth: bool = False     # in/out handles stay collinear while editing


@dataclass(frozen=True)
class Contour:
    points: tuple
    closed: bool = True

    @property
    def segment_count(self):
        n = len(self.points)
        return n if self.closed else max(0, n - 1)

    def segment(self, index):
        """(p0, c0, c1, p1, curved) control points of segment ``index``."""
        n = len(self.points)
        a = self.points[index % n]
        b = self.points[(index + 1) % n]
        if a.curve_out:
            return (a.co, add(a.co, a.handle_out), add(b.co, b.handle_in), b.co, True)
        return (a.co, a.co, b.co, b.co, False)

    def uids(self):
        return tuple(p.uid for p in self.points)


@dataclass(frozen=True)
class PatternShape:
    """Pure snapshot of one pattern: everything the mesher needs."""
    outline: Contour
    holes: tuple = ()
    lines: tuple = ()  # open Contours: internal lines embedded in the mesh
    spacing: float = 0.02
    tolerance: float = 0.001
    location: Vec = (0.0, 0.0)
    rotation: float = 0.0
    mirrored: bool = False

    def key(self):
        """Hashable content key; equal keys always mesh identically."""
        def contour_key(contour):
            return (contour.closed, tuple(
                (p.uid, _r(p.co), _r(p.handle_in), _r(p.handle_out), p.curve_out)
                for p in contour.points))
        return (contour_key(self.outline), tuple(map(contour_key, self.holes)),
                tuple(map(contour_key, self.lines)), round(self.spacing, 9),
                round(self.tolerance, 9))


def _r(v):
    return (round(v[0], 9), round(v[1], 9))


# -- small vector helpers ---------------------------------------------------

def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def scale(a, s):
    return (a[0] * s, a[1] * s)


def length(a):
    return math.hypot(a[0], a[1])


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def rotate(v, angle):
    c, s = math.cos(angle), math.sin(angle)
    return (v[0] * c - v[1] * s, v[0] * s + v[1] * c)


# -- canvas transform -------------------------------------------------------
#
# Pattern-local coordinates are what the physical panel is made of. The
# canvas placement (location, rotation, mirror) only positions the piece on
# the 2D drafting table; it never changes the 3D arrangement.

def to_canvas(local, location, rotation, mirrored=False):
    x, y = local
    if mirrored:
        x = -x
    return add(rotate((x, y), rotation), location)


def to_local(canvas, location, rotation, mirrored=False):
    x, y = rotate(sub(canvas, location), -rotation)
    return (-x, y) if mirrored else (x, y)


def vector_to_local(vector, rotation, mirrored=False):
    x, y = rotate(vector, -rotation)
    return (-x, y) if mirrored else (x, y)


# -- primitives -------------------------------------------------------------

def rectangle_points(width, height, uids):
    """Counter-clockwise rectangle centred on the local origin."""
    w, h = width / 2.0, height / 2.0
    corners = ((-w, -h), (w, -h), (w, h), (-w, h))
    return tuple(ContourPoint(uid, co) for uid, co in zip(uids, corners))


def ellipse_points(radius_x, radius_y, uids):
    """Four smooth Bezier quarter arcs (counter-clockwise, from +X)."""
    kx, ky = radius_x * KAPPA, radius_y * KAPPA
    rows = (((radius_x, 0.0), (0.0, -ky), (0.0, ky)),
            ((0.0, radius_y), (kx, 0.0), (-kx, 0.0)),
            ((-radius_x, 0.0), (0.0, ky), (0.0, -ky)),
            ((0.0, -radius_y), (-kx, 0.0), (kx, 0.0)))
    return tuple(ContourPoint(uid, co, handle_in, handle_out, True, True)
                 for uid, (co, handle_in, handle_out) in zip(uids, rows))


def straighten(point):
    return replace(point, curve_out=False, handle_out=(0.0, 0.0))


def curve_segment(contour, index, bulge=0.25):
    """Convert the straight segment ``index`` into a Bezier with 1/3 handles.

    ``bulge`` offsets both handles perpendicular to the chord (fraction of
    the chord length) so the conversion is visible immediately, like
    dragging the middle of an edge with Edit Curve.
    """
    n = len(contour.points)
    a, b = contour.points[index % n], contour.points[(index + 1) % n]
    chord = sub(b.co, a.co)
    normal = (-chord[1] * bulge, chord[0] * bulge)
    out = add(scale(chord, 1.0 / 3.0), normal)
    back = add(scale(chord, -1.0 / 3.0), normal)
    points = list(contour.points)
    points[index % n] = replace(a, curve_out=True, handle_out=out)
    points[(index + 1) % n] = replace(b, handle_in=back)
    return replace(contour, points=tuple(points))


def signed_area(points):
    area = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return area / 2.0


def centroid(points):
    """Area centroid of a simple polygon (vertex mean when degenerate)."""
    a = signed_area(points)
    if abs(a) < 1e-14:
        n = max(1, len(points))
        return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    cx = cy = 0.0
    n = len(points)
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return (cx / (6.0 * a), cy / (6.0 * a))


def recentre(points, origin):
    """Shift absolute points so ``origin`` becomes the local origin."""
    return tuple(replace(p, co=sub(p.co, origin)) for p in points)


def mirror_points(points):
    """Mirror across the local Y axis, keeping counter-clockwise order."""
    # Mirroring reverses orientation, so the ring is walked backwards. The
    # reversed segment leaving point j is the old segment that entered it:
    # it is curved when the previous point's segment was, its outgoing
    # control comes from j's old incoming handle and vice versa.
    def flip(v):
        return (-v[0], v[1])
    n = len(points)
    result = []
    for j in range(n - 1, -1, -1):
        p, previous = points[j], points[(j - 1) % n]
        result.append(replace(p, co=flip(p.co), handle_out=flip(p.handle_in),
                              handle_in=flip(p.handle_out), curve_out=previous.curve_out))
    return tuple(result)


@dataclass
class UidAllocator:
    next_uid: int = 1
    used: set = field(default_factory=set)

    def take(self, count=1):
        values = []
        for _ in range(count):
            while self.next_uid in self.used:
                self.next_uid += 1
            values.append(self.next_uid)
            self.used.add(self.next_uid)
            self.next_uid += 1
        return values
