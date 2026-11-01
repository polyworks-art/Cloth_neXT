# SPDX-License-Identifier: GPL-3.0-or-later
"""2D Pattern editor drawing (only in the locked PATTERN NeXt view).

Patterns are drawn from the parametric data and the cached mesher result,
not from scene objects: the 2D editor hides every object type, so the 3D
arrangement can never clutter the drafting table. Canvas ``(u, v)`` is
drawn on world ``(u, 0, v)``; the front orthographic view shows it 1:1.
"""

from __future__ import annotations

import logging
import math

import bpy
import numpy as np

from ...pattern_next.curves import display_polyline
from ...pattern_next.seams import SeamSide, side_polyline
from ...pattern_next.shapes import Contour, distance
from ...pattern_next.snapping import nice_grid_step
from ...pattern_next.validation import PatternError
from . import model, sync, workspace

_log = logging.getLogger(__name__)

BACKGROUND = (0.155, 0.16, 0.175, 1.0)
GRID_MINOR = (1.0, 1.0, 1.0, 0.045)
GRID_MAJOR = (1.0, 1.0, 1.0, 0.11)
GRID_AXIS = (0.55, 0.75, 1.0, 0.28)
FABRIC = ((0.36, 0.52, 0.70), (0.42, 0.62, 0.52), (0.66, 0.50, 0.40),
          (0.55, 0.47, 0.70), (0.64, 0.62, 0.40), (0.40, 0.60, 0.66))
OUTLINE = (0.86, 0.90, 0.95, 0.95)
SELECTED = (1.0, 0.62, 0.18, 1.0)
HOVER = (1.0, 1.0, 1.0, 0.75)
INVALID = (1.0, 0.30, 0.30, 1.0)
VALID = (0.35, 0.85, 0.55, 1.0)
WIRE = (0.0, 0.0, 0.0, 0.22)
SEAM_COLORS = ((1.0, 0.82, 0.25), (0.35, 0.85, 1.0), (1.0, 0.45, 0.70), (0.55, 1.0, 0.45),
               (0.85, 0.60, 1.0), (1.0, 0.60, 0.35))
TEXT = (0.92, 0.94, 0.97, 1.0)
TEXT_DIM = (0.70, 0.74, 0.80, 1.0)

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


# -- view helpers -----------------------------------------------------------------

def ui_scale(context=None):
    try:
        return max(0.75, float((context or bpy.context).preferences.system.ui_scale))
    except AttributeError:
        return 1.0


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
    if system == "METRIC" and unit.length_unit in {"ADAPTIVE", "METERS"}:
        # Garment work reads in centimetres.
        return f"{value * unit.scale_length * 100.0:.1f} cm"
    return bpy.utils.units.to_string(system, "LENGTH", value * unit.scale_length, precision=1)


# -- primitive drawing ---------------------------------------------------------------

def _world(points):
    return [(float(u), 0.0, float(v)) for u, v in points]


def _shader(name):
    import gpu
    return gpu.shader.from_builtin(name)


def lines(points_pairs, color, width=1.0, *, region=None):
    """LINES in canvas space; ``points_pairs`` is a flat list of endpoints."""
    if len(points_pairs) < 2:
        return
    from gpu_extras.batch import batch_for_shader
    region = region or bpy.context.region
    shader = _shader("POLYLINE_UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", width * ui_scale())
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": _world(points_pairs)}).draw(shader)


def polyline(points, color, width=1.0, closed=False, region=None):
    if len(points) < 2:
        return
    pairs = []
    count = len(points) if closed else len(points) - 1
    for i in range(count):
        pairs.append(points[i])
        pairs.append(points[(i + 1) % len(points)])
    lines(pairs, color, width, region=region)


def triangles(points, indices, color):
    if not len(indices):
        return
    from gpu_extras.batch import batch_for_shader
    shader = _shader("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": _world(points)},
                     indices=[tuple(map(int, t)) for t in indices]).draw(shader)


def ear_fill(points):
    """Quick fan-free triangulation of a simple polygon for live previews."""
    from mathutils.geometry import tessellate_polygon
    return tessellate_polygon([[(p[0], p[1], 0.0) for p in points]])


def px_rect(x, y, w, h, color):
    from gpu_extras.batch import batch_for_shader
    shader = _shader("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": ((x, y), (x + w, y), (x + w, y + h), (x, y + h))},
                     indices=((0, 1, 2), (0, 2, 3))).draw(shader)


def px_rounded(x, y, w, h, r, color):
    from gpu_extras.batch import batch_for_shader
    r = max(0.0, min(r, w / 2, h / 2))
    points = []
    for cx, cy, start in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0),
                          (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for i in range(7):
            a = math.radians(start + i * 15)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    verts = [(x + w / 2, y + h / 2)] + points
    indices = [(0, i + 1, (i + 1) % len(points) + 1) for i in range(len(points))]
    shader = _shader("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices).draw(shader)


def px_lines(pairs, color, width=1.0, region=None):
    from gpu_extras.batch import batch_for_shader
    region = region or bpy.context.region
    shader = _shader("POLYLINE_UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch_for_shader(shader, "LINES", {"pos": pairs}).draw(shader)


def px_points(points, color, size=6.0, square=True):
    """Small squares (or diamonds) at region pixel positions."""
    if not points:
        return
    half = size / 2.0
    verts, indices = [], []
    for x, y in points:
        base = len(verts)
        if square:
            verts += [(x - half, y - half), (x + half, y - half), (x + half, y + half),
                      (x - half, y + half)]
        else:
            verts += [(x, y - half), (x + half, y), (x, y + half), (x - half, y)]
        indices += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    from gpu_extras.batch import batch_for_shader
    shader = _shader("UNIFORM_COLOR")
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices).draw(shader)


def text(value, x, y, color=TEXT, size=11, shadow=True, align="LEFT"):
    import blf
    import gpu
    size = round(size * ui_scale())
    blf.size(0, size)
    width, height = blf.dimensions(0, value)
    if align == "CENTER":
        x -= width / 2
    elif align == "RIGHT":
        x -= width
    if shadow:
        blf.enable(0, blf.SHADOW)
        blf.shadow(0, 3, 0.0, 0.0, 0.0, 0.85)
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, value)
    if shadow:
        blf.disable(0, blf.SHADOW)
    gpu.state.blend_set("ALPHA")
    return width, height


# -- grid -----------------------------------------------------------------------

def grid_step(context, region, rv3d):
    settings = context.scene.cloth_next_pattern_settings
    if settings.grid_step > 0:
        return settings.grid_step
    return nice_grid_step(pixel_size(region, rv3d), 14.0 * ui_scale(context))


def _draw_grid(context, region, rv3d):
    x0, y0, x1, y1 = canvas_bounds(region, rv3d)
    triangles([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [(0, 1, 2), (0, 2, 3)], BACKGROUND)
    step = grid_step(context, region, rv3d)
    major = step * 10
    minor, majors = [], []
    i0, i1 = math.floor(x0 / step), math.ceil(x1 / step)
    j0, j1 = math.floor(y0 / step), math.ceil(y1 / step)
    if (i1 - i0) + (j1 - j0) > 2000:
        return
    for i in range(i0, i1 + 1):
        x = i * step
        target = majors if i % 10 == 0 else minor
        target += [(x, y0), (x, y1)]
    for j in range(j0, j1 + 1):
        y = j * step
        target = majors if j % 10 == 0 else minor
        target += [(x0, y), (x1, y)]
    lines(minor, GRID_MINOR, 1.0, region=region)
    lines(majors, GRID_MAJOR, 1.0, region=region)
    lines([(0.0, y0), (0.0, y1), (x0, 0.0), (x1, 0.0)], GRID_AXIS, 1.2, region=region)
    return major


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

    geometry = {"outline": place(display_polyline(shape.outline)).tolist(),
                "holes": [place(display_polyline(h)).tolist() for h in shape.holes],
                "lines": [place(display_polyline(l)).tolist() for l in shape.lines]}
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


def pattern_color(index):
    return FABRIC[index % len(FABRIC)]


def is_selected(pattern):
    obj = pattern.mesh_object
    try:
        return bool(obj is not None and obj.select_get())
    except (ReferenceError, RuntimeError):
        return False


def _draw_patterns(context, region, session):
    scene = context.scene
    settings = scene.cloth_next_pattern_settings
    hidden = session.hidden_patterns() if session else set()
    hover = session.hover_pattern() if session else None
    for index, pattern in enumerate(scene.cloth_next_patterns):
        if pattern.identifier in hidden:
            continue
        geometry = _pattern_geometry(scene, pattern)
        selected = is_selected(pattern)
        rgb = pattern_color(index)
        alpha = 0.72 if selected else 0.55
        if "tris" in geometry:
            triangles(geometry["verts"], geometry["tris"], (*rgb, alpha))
            if settings.show_mesh:
                lines(geometry["wire"], WIRE, 1.0, region=region)
        elif len(geometry["outline"]) >= 3:
            triangles(geometry["outline"], ear_fill(geometry["outline"]), (*rgb, 0.25))
        width = 2.4 if selected else 1.6
        color = SELECTED if selected else (HOVER if hover == pattern.identifier else OUTLINE)
        if pattern.status:
            color = INVALID
        polyline(geometry["outline"], color, width, closed=True, region=region)
        for hole in geometry["holes"]:
            polyline(hole, color, width * 0.85, closed=True, region=region)
        for line in geometry["lines"]:
            polyline(line, (0.95, 0.85, 0.45, 0.95), 1.4, region=region)


def seam_color(index):
    return SEAM_COLORS[index % len(SEAM_COLORS)]


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


def _arrow(line, color, region, size):
    if len(line) < 2:
        return
    mid = _resample_line(line, 3)[1]
    tail = _resample_line(line, 9)[3]
    dx, dy = mid[0] - tail[0], mid[1] - tail[1]
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm * size, dy / norm * size
    left = (mid[0] - dx - dy * 0.6, mid[1] - dy + dx * 0.6)
    right = (mid[0] - dx + dy * 0.6, mid[1] - dy - dx * 0.6)
    lines([left, mid, mid, right], color, 2.0, region=region)


def _draw_seams(context, region, rv3d, session):
    scene = context.scene
    if not scene.cloth_next_pattern_settings.show_seams:
        return
    hover = session.hover_seam if session else None
    px = pixel_size(region, rv3d)
    for index, seam in enumerate(scene.cloth_next_pattern_seams):
        sides = seam_sides(scene, seam)
        if sides is None:
            continue
        a, b = sides
        rgb = seam_color(index)
        emphasis = 1.0 if hover in (None, seam.identifier) else 0.55
        color = (*rgb, emphasis) if not seam.status else INVALID
        width = 4.0 if hover == seam.identifier else 3.0
        polyline(a, color, width, region=region)
        polyline(b, color, width, region=region)
        _arrow(a, color, region, 9 * px)
        _arrow(b if not seam.flipped else list(reversed(b)), color, region, 9 * px)
        count = max(2, min(9, int(min(len(a), len(b)))))
        ta, tb = _resample_line(a, count), _resample_line(b, count)
        if seam.flipped:
            tb = list(reversed(tb))
        threads = []
        for p, q in zip(ta, tb):
            threads += [p, q]
        lines(threads, (*rgb, 0.28 * emphasis), 1.0, region=region)


def draw_view():
    context = bpy.context
    if not workspace.is_pattern_area(context):
        return
    try:
        import gpu
        region, rv3d = context.region, context.region_data
        if region is None or rv3d is None:
            return
        _guard_view(rv3d)
        from . import editor
        session = editor.session_for(context.window)
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        gpu.state.depth_mask_set(False)
        _draw_grid(context, region, rv3d)
        _draw_patterns(context, region, session)
        _draw_seams(context, region, rv3d, session)
        if session is not None:
            session.draw_view(context, region, rv3d)
    except Exception:
        _log.exception("PATTERN NeXt 2D draw failed")


def _segment_labels(context, region, rv3d, pattern):
    points = model.outline(pattern)
    contour = Contour(points)
    from ...pattern_next.curves import segment_polyline
    for index in range(contour.segment_count):
        line = model.to_canvas_points(pattern, segment_polyline(contour, index, 0.0005))
        size = sum(distance(p, q) for p, q in zip(line, line[1:]))
        mid = _resample_line(line, 3)[1]
        xy = canvas_to_region(region, rv3d, mid)
        if xy is not None:
            text(format_length(context, size), xy[0], xy[1] + 4, TEXT_DIM, 10, align="CENTER")


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
        from . import editor
        session = editor.session_for(context.window)
        scene = context.scene
        if scene.cloth_next_pattern_settings.show_lengths:
            for pattern in scene.cloth_next_patterns:
                if is_selected(pattern) or (session and session.hover_pattern() == pattern.identifier):
                    _segment_labels(context, region, rv3d, pattern)
        if session is not None:
            session.draw_pixel(context, region, rv3d)
        else:
            from . import toolbar
            toolbar.draw(context, region, None, None)
            text("Front · Orthographic · 2D Pattern  —  click the editor to start drawing",
                 12 * ui_scale(), 12 * ui_scale(), TEXT_DIM, 10)
    except Exception:
        _log.exception("PATTERN NeXt 2D overlay failed")


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
