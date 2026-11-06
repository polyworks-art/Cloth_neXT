# SPDX-License-Identifier: GPL-3.0-or-later
"""Seams on stable pattern edges and their derived mesh correspondence.

A seam side is a boundary interval of one pattern: it starts at the point
with uid ``start_uid`` and follows the outline forward (counter-clockwise
in contour order) to the point with uid ``end_uid``. Point uids never
change when the pattern is remeshed or when points are inserted into the
interval, so Particle Distance changes and edge splits keep the seam.
The mesh vertex path of a side is re-derived from the mesher's boundary
sources each time; generated vertex indices are never stored as identity.
"""

from __future__ import annotations

from dataclasses import dataclass

from .shapes import Contour


class SeamError(ValueError):
    pass


@dataclass(frozen=True)
class SeamSide:
    pattern_id: str
    start_uid: int
    end_uid: int


def segment_side(pattern_id, points, index):
    """The side covering exactly segment ``index`` of an outline."""
    n = len(points)
    return SeamSide(pattern_id, points[index % n].uid, points[(index + 1) % n].uid)


def side_segments(points, side: SeamSide):
    """Segment indices covered by ``side`` (forward along the outline)."""
    uids = [p.uid for p in points]
    try:
        start, end = uids.index(side.start_uid), uids.index(side.end_uid)
    except ValueError as exc:
        raise SeamError("A sewn point was removed from the pattern") from exc
    if start == end:
        raise SeamError("A seam side needs two different points")
    n = len(uids)
    result, i = [], start
    while i != end:
        result.append(i)
        i = (i + 1) % n
    return result


def side_vertices(loop, side: SeamSide):
    """Mesh vertex indices of ``side`` from a mesher BoundaryLoop."""
    corners = {s.segment_uid: k for k, s in enumerate(loop.sources) if s.corner}
    if side.start_uid not in corners or side.end_uid not in corners:
        raise SeamError("A sewn point is missing from the generated boundary")
    start, end = corners[side.start_uid], corners[side.end_uid]
    n = len(loop.vertices)
    path, k = [loop.vertices[start]], start
    while k != end:
        k = (k + 1) % n
        path.append(loop.vertices[k])
    return path


def side_polyline(points, side: SeamSide, tolerance=0.0005):
    from .curves import segment_polyline
    contour = Contour(tuple(points))
    line = []
    for index in side_segments(points, side):
        part = segment_polyline(contour, index, tolerance)
        line.extend(part if not line else part[1:])
    return line


def polyline_length(line):
    from .shapes import distance
    return sum(distance(a, b) for a, b in zip(line, line[1:]))


def sewn_segments(points, sides):
    """Outline segment indices already covered by any of ``sides``.

    Sides whose points no longer exist are ignored (their seam reports its
    own error).
    """
    covered = set()
    for side in sides:
        try:
            covered.update(side_segments(points, side))
        except SeamError:
            continue
    return covered


def overlaps_sewn(points, side: SeamSide, sides):
    """True when ``side`` shares an outline segment with an existing seam side.

    One edge sewn to two others pulls three edges onto one line: the cloth
    overstretches between them (Tim's Dev 2.9.64 scene: edge A sewn to B and
    again to A's neighbouring edge). A shared corner alone is fine.
    """
    return bool(set(side_segments(points, side)) & sewn_segments(points, sides))
