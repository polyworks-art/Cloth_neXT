# SPDX-License-Identifier: GPL-3.0-or-later
"""2D Pattern editor drawing (only in the locked PATTERN NeXt view).

Patterns are drawn from the parametric data and the cached mesher result,
not from scene objects: the 2D editor hides every object type, so the 3D
arrangement can never clutter the drafting table. Canvas ``(u, v)`` is
drawn on world ``(u, 0, v)``; the front orthographic view shows it 1:1.

Visual rules (see docs/validation/PATTERN_NEXT_UI_REDESIGN.md): a calm
theme-derived canvas with an adaptive grid; neutral pattern fills (tinted
only by an assigned material); crisp DPI-scaled outlines, the accent only
for selection; triangulation off by default and drawn faint; seams shown as
coloured edges everywhere but with direction and connectors only where
they matter (selected patterns, hovered or chosen seam); measurements only
for the selection and only where they fit.
"""

from __future__ import annotations

import logging
import math

import bpy
import numpy as np

from ...pattern_next.curves import display_polyline, segment_polyline
from ...pattern_next.seams import SeamSide, side_polyline, side_vertices
from ...pattern_next.shapes import Contour, centroid, distance
from ...pattern_next.snapping import nice_grid_step
from ...pattern_next.validation import PatternError
from . import model, style, sync, workspace

_log = logging.getLogger(__name__)

_handles = []
_batches = {}
_guard_pending = False


def tag_redraw():
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except (AttributeError, ReferenceError):
        pass


def clear_cache():
    _batches.clear()


ui_scale = style.ui_scale


# -- view helpers -----------------------------------------------------------------

def region_to_canvas(region, rv3d, xy):
    from bpy_extras.view3d_utils import region_2d_to_location_3d
    p = region_2d_to_location_3d(region, rv3d, xy, (0.0, 0.0, 0.0))
    return (p.x, p.z)


def canvas_to_region(region, rv3d, uv):
    from bpy_extras.view3d_utils import location_3d_to_region_2d
    p = location_3d_to_region_2d(region, rv3d, (uv[0], 0.0, uv[1]))
    return None if p is None else (p.x, p.y)


def pixel_size(region, rv3d):
    a = region_to_canvas(region, rv3d, (0.0, 0.0))
    b = region_to_canvas(region, rv3d, (100.0, 0.0))
    return max(distance(a, b) / 100.0, 1e-9)


def canvas_bounds(region, rv3d):
    a = region_to_canvas(region, rv3d, (0.0, 0.0))
    b = region_to_canvas(region, rv3d, (region.width, region.height))
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def format_length(context, value):
    unit = context.scene.unit_settings
    system = unit.system if unit.system != "NONE" else "METRIC"
    if system == "METRIC" and unit.length_unit in {"ADAPTIVE", "METERS", "CENTIMETERS"}:
        # Garment work reads in centimetres.
        return f"{value * unit.scale_length * 100.0:.1f} cm"
    return bpy.utils.units.to_string(system, "LENGTH", value * unit.scale_length, precision=1)


# -- canvas-space primitives --------------------------------------------------------

def _world(points):
    return [(float(u), 0.0, float(v)) for u, v in points]


def lines(pairs, color, width=1.0, *, region=None):
    """LINES in canvas space; ``pairs`` is a flat list of endpoints."""
    if len(pairs) < 2:
        return
    from gpu_extras.batch import batch_for_shader
    import gpu
    region = region or bpy.context.region
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", width * ui_scale())
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": _world(pairs)}).draw(shader)


def polyline(points, color, width=1.0, closed=False, region=None):
    if len(points) < 2:
        return
    pairs = []
    count = len(points) if closed else len(points) - 1
    for i in range(count):
        pairs.append(points[i])
        pairs.append(points[(i + 1) % len(points)])
    lines(pairs, color, width, region=region)


def dashed(points, color, width, dash, region=None):
    """Polyline with dashes of ``dash`` canvas units (gaps equal)."""
    pairs, carry, on = [], 0.0, True
    for p, q in zip(points, points[1:]):
        length = distance(p, q)
        t = 0.0
        while t < length:
            step = min(dash - carry, length - t)
            a = (p[0] + (q[0] - p[0]) * t / length, p[1] + (q[1] - p[1]) * t / length)
            b = (p[0] + (q[0] - p[0]) * (t + step) / length,
                 p[1] + (q[1] - p[1]) * (t + step) / length)
            if on:
                pairs += [a, b]
            t += step
            carry += step
            if carry >= dash - 1e-12:
                carry, on = 0.0, not on
    lines(pairs, color, width, region=region)


def triangles(points, indices, color):
    if not len(indices):
        return
    from gpu_extras.batch import batch_for_shader
    import gpu
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": _world(points)},
                     indices=[tuple(map(int, t)) for t in indices]).draw(shader)


def ear_fill(points):
    """Quick triangulation of a simple polygon for live previews."""
    from mathutils.geometry import tessellate_polygon
    return tessellate_polygon([[(p[0], p[1], 0.0) for p in points]])


# -- grid ---------------------------------------------------------------------------

def grid_step(context, region, rv3d):
    settings = context.scene.cloth_next_pattern_settings
    if settings.grid_step > 0:
        return settings.grid_step
    return nice_grid_step(pixel_size(region, rv3d), 14.0 * ui_scale(context))


def _draw_grid(context, region, rv3d, pal):
    x0, y0, x1, y1 = canvas_bounds(region, rv3d)
    triangles([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [(0, 1, 2), (0, 2, 3)], pal.canvas)
    if not context.scene.cloth_next_pattern_settings.show_grid:
        return
    px = pixel_size(region, rv3d)
    step = nice_grid_step(px, 9.0 * ui_scale(context))
    spacing = step / px
    # Minor lines fade in with their on-screen spacing; majors (x10) stay.
    fade = max(0.0, min(1.0, (spacing - 8.0) / 22.0))
    minor, majors = [], []
    i0, i1 = math.floor(x0 / step), math.ceil(x1 / step)
    j0, j1 = math.floor(y0 / step), math.ceil(y1 / step)
    if (i1 - i0) + (j1 - j0) > 3000:
        return
    for i in range(i0, i1 + 1):
        (majors if i % 10 == 0 else minor).extend(((i * step, y0), (i * step, y1)))
    for j in range(j0, j1 + 1):
        (majors if j % 10 == 0 else minor).extend(((x0, j * step), (x1, j * step)))
    if fade > 0:
        lines(minor, (*pal.grid_minor[:3], pal.grid_minor[3] * fade), 1.0, region=region)
    lines(majors, pal.grid_major, 1.0, region=region)
    lines([(0.0, y0), (0.0, y1), (x0, 0.0), (x1, 0.0)], pal.axis, 1.0, region=region)


# -- patterns -----------------------------------------------------------------------

def _pattern_geometry(scene, pattern):
    """Canvas-space fill/wire/outline arrays, cached per shape and placement."""
    shape = model.shape(pattern)
    key = (shape.key(), tuple(pattern.location), round(pattern.rotation, 9))
    cached = _batches.get(pattern.identifier)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        result = sync.result_for(scene, pattern)
    except PatternError:
        result = None
    (lx, ly), angle = model.placement(pattern)
    c, s = math.cos(angle), math.sin(angle)

    def place(points):
        p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        return np.stack((p[:, 0] * c - p[:, 1] * s + lx, p[:, 0] * s + p[:, 1] * c + ly), axis=1)

    outline = place(display_polyline(shape.outline)).tolist()
    geometry = {"outline": outline,
                "holes": [place(display_polyline(h)).tolist() for h in shape.holes],
                "lines": [place(display_polyline(l)).tolist() for l in shape.lines],
                "centre": tuple(place([centroid(display_polyline(shape.outline))])[0]),
                "bounds": (min(p[0] for p in outline), min(p[1] for p in outline),
                           max(p[0] for p in outline), max(p[1] for p in outline))}
    if result is not None:
        verts = place(result.vertices)
        tris = result.triangles
        edges = np.concatenate((tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]))
        edges = np.unique(np.sort(edges, axis=1), axis=0)
        geometry["verts"] = verts.tolist()
        geometry["tris"] = tris.tolist()
        geometry["wire"] = verts[edges.ravel()].tolist()
    _batches[pattern.identifier] = (key, geometry)
    return geometry


def is_selected(pattern):
    obj = pattern.mesh_object
    try:
        return bool(obj is not None and obj.select_get())
    except (ReferenceError, RuntimeError):
        return False


def visible_patterns(scene):
    return [p for p in scene.cloth_next_patterns if not p.hidden]


def _draw_patterns(context, region, pal, session):
    scene = context.scene
    settings = scene.cloth_next_pattern_settings
    hover = session.hover_pattern() if session else None
    for pattern in visible_patterns(scene):
        geometry = _pattern_geometry(scene, pattern)
        selected = is_selected(pattern)
        fill = style.fabric_fill(pal, pattern.material, selected)
        if "tris" in geometry:
            triangles(geometry["verts"], geometry["tris"], fill)
            if settings.show_mesh:
                lines(geometry["wire"], pal.wire, 1.0, region=region)
        elif len(geometry["outline"]) >= 3:
            triangles(geometry["outline"], ear_fill(geometry["outline"]), (*fill[:3], 0.4))
        if pattern.status:
            color, width = (*style.ERROR, 1.0), 1.6
        elif selected:
            color, width = pal.outline_selected, 2.0
        elif hover == pattern.identifier:
            color, width = style.mix(pal.outline, pal.outline_selected, 0.45), 1.6
        else:
            color, width = pal.outline, 1.25
        polyline(geometry["outline"], color, width, closed=True, region=region)
        for hole in geometry["holes"]:
            polyline(hole, color, width, closed=True, region=region)
        for line in geometry["lines"]:
            dashed(line, (*color[:3], 0.85), 1.1, 6 * pixel_size(region, context.region_data),
                   region=region)


# -- seams ---------------------------------------------------------------------------

def seam_sides(scene, seam):
    pa, pb = model.find(scene, seam.a_pattern), model.find(scene, seam.b_pattern)
    if pa is None or pb is None:
        return None
    try:
        a = side_polyline(model.outline(pa), SeamSide(pa.identifier, seam.a_start, seam.a_end))
        b = side_polyline(model.outline(pb), SeamSide(pb.identifier, seam.b_start, seam.b_end))
    except ValueError:
        return None
    return model.to_canvas_points(pa, a), model.to_canvas_points(pb, b)


def seam_color(index):
    return style.seam_color(index)


def _resample_line(line, count):
    lengths = [0.0]
    for p, q in zip(line, line[1:]):
        lengths.append(lengths[-1] + distance(p, q))
    total = lengths[-1] or 1.0
    result, j = [], 0
    for k in range(count):
        target = total * k / max(1, count - 1)
        while j < len(lengths) - 2 and lengths[j + 1] < target:
            j += 1
        span = lengths[j + 1] - lengths[j] or 1.0
        t = (target - lengths[j]) / span
        p, q = line[j], line[j + 1]
        result.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return result


def line_length(line):
    return sum(distance(p, q) for p, q in zip(line, line[1:]))


def _chevron(line, color, region, size, at=0.5):
    if len(line) < 2:
        return
    count = 21
    samples = _resample_line(line, count)
    k = max(1, min(count - 1, round(at * (count - 1))))
    tip, tail = samples[k], samples[k - 1]
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm * size, dy / norm * size
    left = (tip[0] - dx - dy * 0.7, tip[1] - dy + dx * 0.7)
    right = (tip[0] - dx + dy * 0.7, tip[1] - dy - dx * 0.7)
    lines([left, tip, tip, right], color, 1.6, region=region)


def _notches(line, color, region, size):
    """Short ticks across both ends of a seam side (drafting notches)."""
    pairs = []
    for end, inner in ((line[0], line[1]), (line[-1], line[-2])):
        dx, dy = inner[0] - end[0], inner[1] - end[1]
        norm = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / norm * size, dx / norm * size
        pairs += [(end[0] - nx, end[1] - ny), (end[0] + nx, end[1] + ny)]
    lines(pairs, color, 1.4, region=region)


def emphasized_seams(context, session):
    scene = context.scene
    chosen = {p.identifier for p in scene.cloth_next_patterns if is_selected(p)}
    result = set()
    tool = context.window_manager.cloth_next_pattern_ui.tool
    seams = scene.cloth_next_pattern_seams
    for index, seam in enumerate(seams):
        if seam.a_pattern in chosen or seam.b_pattern in chosen:
            result.add(seam.identifier)
        if tool == "SEWING" and index == scene.cloth_next_pattern_settings.seam_index:
            result.add(seam.identifier)
    if session is not None and session.hover_seam:
        result.add(session.hover_seam)
    return result


def _draw_seams(context, region, rv3d, pal, session):
    scene = context.scene
    if not scene.cloth_next_pattern_settings.show_seams:
        return
    hidden = {p.identifier for p in scene.cloth_next_patterns if p.hidden}
    px = pixel_size(region, rv3d)
    strong = emphasized_seams(context, session)
    for index, seam in enumerate(scene.cloth_next_pattern_seams):
        if seam.a_pattern in hidden or seam.b_pattern in hidden:
            continue
        sides = seam_sides(scene, seam)
        if sides is None:
            continue
        a, b = sides
        rgb = style.seam_color(index)
        emphasized = seam.identifier in strong
        color = (*style.ERROR, 1.0) if seam.status else (*rgb, 1.0 if emphasized else 0.8)
        width = 3.0 if emphasized else 2.2
        for side in (a, b):
            polyline(side, color, width, region=region)
            _notches(side, color, region, 4 * px)
        if not emphasized:
            continue
        _chevron(a, color, region, 6 * px)
        _chevron(b if not seam.flipped else list(reversed(b)), color, region, 6 * px)
        ta, tb = _resample_line(a, 3), _resample_line(b, 3)
        if seam.flipped:
            tb = list(reversed(tb))
        for p, q in zip(ta, tb):
            dashed([p, q], (*rgb, 0.55), 1.0, 5 * px, region=region)


def _draw_seam_labels(context, region, rv3d, pal, session):
    """Length mismatch of emphasized seams (sewing tolerance hint)."""
    scene = context.scene
    if not scene.cloth_next_pattern_settings.show_seams:
        return
    strong = emphasized_seams(context, session)
    s = ui_scale(context)
    for seam in scene.cloth_next_pattern_seams:
        if seam.identifier not in strong:
            continue
        sides = seam_sides(scene, seam)
        if sides is None:
            continue
        la, lb = line_length(sides[0]), line_length(sides[1])
        diff = abs(la - lb)
        if diff < 0.003 or diff / max(la, lb, 1e-9) < 0.02:
            continue
        mid = _resample_line(sides[0], 3)[1]
        xy = canvas_to_region(region, rv3d, mid)
        if xy is not None:
            style.label(f"Δ {format_length(context, diff)}", xy[0], xy[1] + 10 * s, pal,
                        (*style.WARNING, 1.0), 10, scale=s)


# -- labels ---------------------------------------------------------------------------

def _segment_labels(context, region, rv3d, pal, pattern, only=None):
    """Edge lengths outside the outline, skipped where they would not fit."""
    s = ui_scale(context)
    points = model.outline(pattern)
    contour = Contour(points)
    ring = model.canvas_outline(pattern, 0.002)
    clockwise = sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(ring, ring[1:] + ring[:1])) < 0
    placed = []
    for index in range(contour.segment_count):
        if only is not None and index != only:
            continue
        line = model.to_canvas_points(pattern, segment_polyline(contour, index, 0.0005))
        size = line_length(line)
        a = canvas_to_region(region, rv3d, line[0])
        b = canvas_to_region(region, rv3d, line[-1])
        mid_c = _resample_line(line, 3)[1]
        mid = canvas_to_region(region, rv3d, mid_c)
        if a is None or b is None or mid is None:
            continue
        value = format_length(context, size)
        width = style.text_width(value, 10, s) + 6 * s
        if only is None and math.hypot(b[0] - a[0], b[1] - a[1]) < width + 10 * s:
            continue
        dx, dy = b[0] - a[0], b[1] - a[1]
        norm = math.hypot(dx, dy) or 1.0
        nx, ny = (dy / norm, -dx / norm) if not clockwise else (-dy / norm, dx / norm)
        offset = 9 * s + (abs(nx) * width / 2 + abs(ny) * 7 * s)
        x, y = mid[0] + nx * offset, mid[1] + ny * offset - 5 * s
        box = (x - width / 2, y - 3 * s, x + width / 2, y + 12 * s)
        if any(not (box[2] < o[0] or box[0] > o[2] or box[3] < o[1] or box[1] > o[3])
               for o in placed):
            continue
        placed.append(box)
        style.label(value, x, y, pal, pal.text, 10, scale=s)


def _pattern_names(context, region, rv3d, pal):
    if not context.scene.cloth_next_pattern_settings.show_names:
        return
    s = ui_scale(context)
    for pattern in visible_patterns(context.scene):
        geometry = _pattern_geometry(context.scene, pattern)
        x0, y0, x1, y1 = geometry["bounds"]
        a = canvas_to_region(region, rv3d, (x0, y0))
        b = canvas_to_region(region, rv3d, (x1, y1))
        centre = canvas_to_region(region, rv3d, geometry["centre"])
        if a is None or b is None or centre is None:
            continue
        width = style.text_width(pattern.name, 10, s)
        if abs(b[0] - a[0]) < width + 16 * s or abs(b[1] - a[1]) < 24 * s:
            continue
        fill = style.fabric_fill(pal, pattern.material, is_selected(pattern))
        ink = (0.08, 0.09, 0.10, 0.78) if style._luma(fill) > 0.4 else (0.95, 0.96, 0.97, 0.85)
        style.text(pattern.name, centre[0], centre[1] - 4 * s, ink, 10, "CENTER", s)


# -- entry points ---------------------------------------------------------------------

def draw_view():
    context = bpy.context
    if not workspace.is_pattern_area(context):
        _draw_view_3d(context)
        return
    try:
        import gpu
        region, rv3d = context.region, context.region_data
        if region is None or rv3d is None:
            return
        _guard_view(rv3d)
        from . import editor
        session = editor.session_for(context.window)
        pal = style.palette(context)
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(False)
        _draw_grid(context, region, rv3d, pal)
        _draw_patterns(context, region, pal, session)
        _draw_seams(context, region, rv3d, pal, session)
        if session is not None:
            session.draw_view(context, region, rv3d)
    except Exception:
        _log.exception("PATTERN NeXt 2D draw failed")


def draw_pixel():
    context = bpy.context
    if not workspace.is_pattern_area(context):
        return
    try:
        import gpu
        region, rv3d = context.region, context.region_data
        if region is None or rv3d is None:
            return
        gpu.state.blend_set("ALPHA")
        from . import editor, toolbar
        session = editor.session_for(context.window)
        scene = context.scene
        pal = style.palette(context)
        _pattern_names(context, region, rv3d, pal)
        if scene.cloth_next_pattern_settings.show_lengths:
            for pattern in visible_patterns(scene):
                if is_selected(pattern):
                    _segment_labels(context, region, rv3d, pal, pattern)
        _draw_seam_labels(context, region, rv3d, pal, session)
        if not scene.cloth_next_patterns:
            s = ui_scale(context)
            cx, cy = region.width / 2, region.height / 2
            style.text("Draw a pattern", cx, cy + 6 * s, pal.text_dim, 13, "CENTER", s)
            style.text("Polygon H  ·  Rectangle S  ·  Ellipse E  ·  Curve C", cx, cy - 14 * s,
                       (*pal.text_dim[:3], 0.7), 10, "CENTER", s)
        if session is not None:
            session.draw_pixel(context, region, rv3d)
        else:
            from . import dock
            dock.draw(context, region)
            toolbar.draw(context, region)
            tool = context.window_manager.cloth_next_pattern_ui.tool
            toolbar.draw_status(context, region, tool, "Move the mouse into the editor to start",
                                right_text="Front · Ortho")
    except Exception:
        _log.exception("PATTERN NeXt 2D overlay failed")


def _draw_view_3d(context):
    """Restrained seam display on the panels in the workspace's 3D view."""
    try:
        if not workspace.is_pattern_workspace(getattr(context, "workspace", None)):
            return
        scene = context.scene
        if not scene.cloth_next_pattern_settings.show_seams or not scene.cloth_next_pattern_seams:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        strong = emphasized_seams(context, None)
        if not strong:
            return
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        region = context.region
        gpu.state.blend_set("ALPHA")
        for index, seam in enumerate(scene.cloth_next_pattern_seams):
            if seam.identifier not in strong or seam.status:
                continue
            pa, pb = model.find(scene, seam.a_pattern), model.find(scene, seam.b_pattern)
            if pa is None or pb is None or not sync.object_alive(scene, pa) \
                    or not sync.object_alive(scene, pb):
                continue
            for pattern, start, end in ((pa, seam.a_start, seam.a_end),
                                        (pb, seam.b_start, seam.b_end)):
                if pattern.hidden:
                    continue
                result = sync.result_for(scene, pattern)
                ids = side_vertices(result.outline, SeamSide(pattern.identifier, start, end))
                matrix = pattern.mesh_object.matrix_world
                from mathutils import Vector
                pts = [matrix @ Vector((result.vertices[i][0], 0.0, result.vertices[i][1]))
                       for i in ids]
                pairs = []
                for p, q in zip(pts, pts[1:]):
                    pairs += [tuple(p), tuple(q)]
                shader.bind()
                shader.uniform_float("viewportSize", (region.width, region.height))
                shader.uniform_float("lineWidth", 2.5 * ui_scale(context))
                shader.uniform_float("color", (*style.seam_color(index), 0.95))
                batch_for_shader(shader, "LINES", {"pos": pairs}).draw(shader)
    except Exception:
        _log.exception("PATTERN NeXt 3D seam overlay failed")


def _guard_view(rv3d):
    """Restore front-ortho if anything (a script, a future shortcut) drifted."""
    global _guard_pending
    if workspace.is_front_ortho(rv3d) and rv3d.lock_rotation or _guard_pending:
        return
    _guard_pending = True

    def fix():
        global _guard_pending
        _guard_pending = False
        for window in bpy.context.window_manager.windows:
            for area in workspace.window_pattern_areas(window, repair=True):
                workspace.configure_2d(area.spaces.active)
                area.tag_redraw()
        return None
    bpy.app.timers.register(fix, first_interval=0.0)


def register():
    if _handles:
        return
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw_view, (), "WINDOW", "POST_VIEW"))
    _handles.append(bpy.types.SpaceView3D.draw_handler_add(draw_pixel, (), "WINDOW", "POST_PIXEL"))


def unregister():
    while _handles:
        bpy.types.SpaceView3D.draw_handler_remove(_handles.pop(), "WINDOW")
    _batches.clear()
    style.clear_textures()
