# SPDX-License-Identifier: GPL-3.0-or-later
"""Conversion between PATTERN NeXt RNA data and the pure geometry core."""

from __future__ import annotations

import uuid

from ...pattern_next import SCHEMA_VERSION
from ...pattern_next.curves import display_polyline
from ...pattern_next.shapes import (Contour, ContourPoint, PatternShape, centroid,
                                    ellipse_points, rectangle_points, recentre, to_canvas)

MESH_TOLERANCE = 0.001  # 1 mm chord deviation on curved boundaries


def read_points(collection):
    return tuple(ContourPoint(int(p.uid), (p.co[0], p.co[1]), (p.handle_in[0], p.handle_in[1]),
                              (p.handle_out[0], p.handle_out[1]), bool(p.curve_out),
                              bool(p.smooth)) for p in collection)


def write_points(collection, points):
    """Replace ``collection`` with ``points`` (resizing in place)."""
    while len(collection) > len(points):
        collection.remove(len(collection) - 1)
    while len(collection) < len(points):
        collection.add()
    for item, p in zip(collection, points):
        item.uid = p.uid
        item.co = p.co
        item.handle_in = p.handle_in
        item.handle_out = p.handle_out
        item.curve_out = p.curve_out
        item.smooth = p.smooth


def outline(pattern):
    return read_points(pattern.points)


def contours(pattern, kind):
    return [read_points(c.points) for c in pattern.contours if c.kind == kind and len(c.points)]


def shape(pattern, spacing=None):
    return PatternShape(
        Contour(outline(pattern)),
        holes=tuple(Contour(points) for points in contours(pattern, "HOLE")),
        lines=tuple(Contour(points, False) for points in contours(pattern, "LINE")),
        spacing=float(spacing if spacing is not None else pattern.particle_distance),
        tolerance=MESH_TOLERANCE,
        location=(pattern.location[0], pattern.location[1]),
        rotation=float(pattern.rotation))


def placement(pattern):
    return (pattern.location[0], pattern.location[1]), float(pattern.rotation)


def to_canvas_points(pattern, local_points):
    location, rotation = placement(pattern)
    return [to_canvas(p, location, rotation) for p in local_points]


def canvas_outline(pattern, tolerance=0.0005):
    return to_canvas_points(pattern, display_polyline(Contour(outline(pattern)), tolerance))


def canvas_rings(pattern, tolerance=0.0005):
    rings = [canvas_outline(pattern, tolerance)]
    for points in contours(pattern, "HOLE"):
        rings.append(to_canvas_points(pattern, display_polyline(Contour(points), tolerance)))
    return rings


def allocate(pattern, count=1):
    used = {p.uid for p in pattern.points}
    for c in pattern.contours:
        used.add(c.uid)
        used.update(p.uid for p in c.points)
    values, candidate = [], max([pattern.next_uid, *[u + 1 for u in used]])
    for _ in range(count):
        values.append(candidate)
        candidate += 1
    pattern.next_uid = candidate
    return values


def find(scene, identifier):
    if not identifier:
        return None
    for pattern in scene.cloth_next_patterns:
        if pattern.identifier == identifier:
            return pattern
    return None


def index_of(scene, pattern):
    for i, item in enumerate(scene.cloth_next_patterns):
        if item.identifier == pattern.identifier:
            return i
    return -1


def for_object(scene, obj):
    if obj is None:
        return None
    for pattern in scene.cloth_next_patterns:
        if pattern.mesh_object == obj:
            return pattern
    return None


def unique_name(scene, base):
    names = {p.name for p in scene.cloth_next_patterns}
    if base not in names:
        return base
    index = 1
    while f"{base}.{index:03d}" in names:
        index += 1
    return f"{base}.{index:03d}"


def new_pattern(scene, name, canvas_points, kind="POLYGON", size=(0.0, 0.0),
                particle_distance=None):
    """Create a pattern from absolute canvas points (uids are assigned here).

    The pattern-local origin is the area centroid, so the panel pivots
    around its middle in both editors.
    """
    name = unique_name(scene, name)  # before add(): the new item is "Pattern" too
    pattern = scene.cloth_next_patterns.add()
    pattern.identifier = uuid.uuid4().hex
    pattern.schema_version = SCHEMA_VERSION
    pattern.name = name
    pattern.particle_distance = float(
        particle_distance if particle_distance is not None
        else scene.cloth_next_pattern_settings.default_particle_distance)
    uids = allocate(pattern, len(canvas_points))
    points = tuple(ContourPoint(uid, p.co, p.handle_in, p.handle_out, p.curve_out, p.smooth)
                   for uid, p in zip(uids, canvas_points))
    origin = centroid(display_polyline(Contour(points)))
    write_points(pattern.points, recentre(points, origin))
    pattern["_pn_suspend"] = 1  # placement update must not rebuild mid-creation
    pattern.location = origin
    if kind in {"RECTANGLE", "ELLIPSE"}:
        pattern.shape_kind = kind
        pattern.primitive_size = size
    del pattern["_pn_suspend"]
    return pattern


def apply_primitive(pattern):
    """Regenerate a rectangle/ellipse from its size, keeping point uids."""
    if pattern.get("_pn_suspend") or pattern.shape_kind not in {"RECTANGLE", "ELLIPSE"}:
        return
    uids = [p.uid for p in pattern.points]
    if len(uids) != 4:
        return
    width, height = pattern.primitive_size
    points = (rectangle_points(width, height, uids) if pattern.shape_kind == "RECTANGLE"
              else ellipse_points(width / 2.0, height / 2.0, uids))
    write_points(pattern.points, points)


def rectangle(width, height):
    return rectangle_points(width, height, range(1, 5))


def ellipse(width, height):
    return ellipse_points(width / 2.0, height / 2.0, range(1, 5))


def translate(points, offset):
    return tuple(ContourPoint(p.uid, (p.co[0] + offset[0], p.co[1] + offset[1]), p.handle_in,
                              p.handle_out, p.curve_out, p.smooth) for p in points)


def make_free_form(pattern):
    """A point edit turns a parametric primitive into a free outline."""
    if pattern.shape_kind != "POLYGON":
        pattern["_pn_suspend"] = 1
        pattern.shape_kind = "POLYGON"
        del pattern["_pn_suspend"]


def migrate(scene):
    """Forward-only schema migration; unknown future versions are kept."""
    settings = scene.cloth_next_pattern_settings
    for pattern in scene.cloth_next_patterns:
        if pattern.schema_version < SCHEMA_VERSION:
            # v1 is the first schema; later versions add steps here.
            pattern.schema_version = SCHEMA_VERSION
    if settings.schema_version < SCHEMA_VERSION:
        settings.schema_version = SCHEMA_VERSION
