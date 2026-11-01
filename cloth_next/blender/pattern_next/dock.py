# SPDX-License-Identifier: GPL-3.0-or-later
"""The docked PATTERN NeXt inspector (right edge of the 2D Pattern editor).

Not Blender's N sidebar: a dedicated column that belongs to the 2D editor,
like a garment application's property editor. It lists the patterns and
shows the properties of what is selected (overview, pattern, several
patterns, point, edge, seam). Values are edited with Blender's own widgets:
clicking a value opens a small native popover with exactly that property,
so units, dragging, typing and undo behave like everywhere in Blender.

Drawing and hit testing share one row layout, rebuilt on every draw from
the current selection; the modal editor forwards clicks, hover and wheel
scrolling inside the column.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import bpy

from . import inspector, model, style
from .properties import TOOL_INFO

WIDTH = 248
ROW = 22
HEADER = 26

_state = {"scroll": 0.0, "collapsed": {"Geometry", "Arrangement"}, "edit": None,
          "hover": None, "rows": None}


@dataclass
class Row:
    kind: str                     # header, field, buttons, list, info, toggle
    label: str = ""
    value: str = ""
    icon: str = ""
    action: object = None         # callable(context) for clicks
    items: tuple = ()             # buttons: (label, icon, action, active, enabled)
    active: bool = False
    color: tuple = None
    toggle: object = None         # list rows: eye action
    hidden: bool = False
    key: str = ""
    y: float = 0.0
    h: float = 0.0
    hits: list = field(default_factory=list)


# -- geometry ---------------------------------------------------------------------

def width(context, region):
    s = style.ui_scale(context)
    return min(WIDTH * s, max(180 * s, region.width * 0.42))


def visible(context):
    return bool(context.scene.cloth_next_pattern_settings.show_inspector)


def rect(context, region):
    """(x, y, w, h) of the column, or None when collapsed."""
    if not visible(context):
        return None
    from ..tool_overlay import header_overlap, sidebar_overlap
    from .toolbar import status_rect
    s = style.ui_scale(context)
    right = region.width - sidebar_overlap(context)
    w = width(context, region)
    top = region.height - header_overlap(context)
    bottom = status_rect(context, region)[3]
    del s
    return (right - w, bottom, w, top - bottom)


# -- actions ---------------------------------------------------------------------

def _edit(target_kind, identifier, prop, index=-1, label=""):
    def run(context):
        _state["edit"] = (target_kind, identifier, prop, index, label)
        bpy.ops.wm.call_panel(name="CLOTHNEXT_PT_pattern_value_popover", keep_open=False)
    return run


def _op(name, **kwargs):
    def run(context):
        namespace, operator = name.split(".")
        getattr(getattr(bpy.ops, namespace), operator)("INVOKE_DEFAULT", **kwargs)
    return run


def _select(identifier):
    def run(context):
        from .editor import select_pattern
        pattern = model.find(context.scene, identifier)
        if pattern is not None:
            select_pattern(context, pattern)
            inspector.follow_selection(context)
            inspector.refresh(context)
    return run


def _toggle_hidden(identifier):
    def run(context):
        pattern = model.find(context.scene, identifier)
        if pattern is not None:
            pattern.hidden = not pattern.hidden
            bpy.ops.ed.undo_push(message="Pattern Visibility")
    return run


def _collapse(title):
    def run(_context):
        collapsed = _state["collapsed"]
        collapsed.symmetric_difference_update({title})
    return run


def _seam_select(index):
    def run(context):
        context.scene.cloth_next_pattern_settings.seam_index = index
        context.window_manager.cloth_next_pattern_ui.tool = "SEWING"
    return run


def _set_tool(key):
    def run(context):
        context.window_manager.cloth_next_pattern_ui.tool = key
    return run


# -- content ------------------------------------------------------------------------

def _length(context, value):
    from .draw import format_length
    return format_length(context, value)


def _section(rows, title, icon=""):
    collapsed = title in _state["collapsed"]
    rows.append(Row("header", title, icon=icon, active=not collapsed, action=_collapse(title)))
    return not collapsed


def build(context):
    scene = context.scene
    current = inspector.selection(context)
    rows = []
    if _section(rows, f"Patterns  ·  {len(scene.cloth_next_patterns)}", "pattern"):
        from .draw import is_selected
        for pattern in scene.cloth_next_patterns:
            rows.append(Row("list", pattern.name, icon="pattern", active=is_selected(pattern),
                            hidden=pattern.hidden, action=_select(pattern.identifier),
                            toggle=_toggle_hidden(pattern.identifier),
                            color=(*style.ERROR, 1.0) if pattern.status else None,
                            key=pattern.identifier))
        if not scene.cloth_next_patterns:
            rows.append(Row("info", "No patterns yet"))
    getattr(_Context, current.kind.lower())(context, rows, current)
    return rows


class _Context:
    @staticmethod
    def none(context, rows, _current):
        scene = context.scene
        if _section(rows, "Create", "polygon"):
            buttons = []
            for key in ("POLYGON", "RECTANGLE", "ELLIPSE", "CURVE_DRAW"):
                info = next(t for t in TOOL_INFO if t[0] == key)
                buttons.append(("", info[4], _set_tool(key),
                                context.window_manager.cloth_next_pattern_ui.tool == key, True))
            rows.append(Row("buttons", items=tuple(buttons)))
            settings = scene.cloth_next_pattern_settings
            rows.append(Row("field", "Particle Dist.", _length(context, settings.default_particle_distance),
                            action=_edit("settings", "", "default_particle_distance")))
            area = sum(p.area for p in scene.cloth_next_patterns)
            rows.append(Row("field", "Fabric area", f"{area:.3f} m²"))
            rows.append(Row("field", "Seams", str(len(scene.cloth_next_pattern_seams))))

    @staticmethod
    def pattern(context, rows, current):
        pattern = current.pattern
        ident = pattern.identifier
        if _section(rows, pattern.name, "pattern"):
            rows.append(Row("field", "Name", pattern.name, action=_edit("pattern", ident, "name")))
            w, h = inspector.dimensions(pattern)
            if pattern.shape_kind in {"RECTANGLE", "ELLIPSE"}:
                rows.append(Row("field", "Width", _length(context, pattern.primitive_size[0]),
                                action=_edit("pattern", ident, "primitive_size", 0, "Width")))
                rows.append(Row("field", "Height", _length(context, pattern.primitive_size[1]),
                                action=_edit("pattern", ident, "primitive_size", 1, "Height")))
            else:
                rows.append(Row("field", "Size", f"{_length(context, w)} × {_length(context, h)}"))
            rows.append(Row("field", "Area", f"{pattern.area * 1e4:.1f} cm²"))
            rows.append(Row("field", "Particle Dist.", _length(context, pattern.particle_distance),
                            action=_edit("pattern", ident, "particle_distance")))
            rows.append(Row("field", "Material", pattern.material.name if pattern.material else "—",
                            action=_edit("pattern", ident, "material")))
            if pattern.status:
                rows.append(Row("info", pattern.status, color=(*style.ERROR, 1.0)))
        seams = inspector.seams_of(context.scene, pattern)
        if _section(rows, f"Sewing  ·  {len(seams)}", "sewing"):
            if not seams:
                rows.append(Row("info", "No seams · Sewing tool M"))
            for index, seam in seams:
                other = model.find(context.scene, seam.b_pattern if seam.a_pattern == ident
                                   else seam.a_pattern)
                lengths = inspector.seam_lengths(context.scene, seam)
                rows.append(Row("field", f"→ {other.name if other else '?'}",
                                _length(context, lengths[0]) if lengths else "—",
                                color=(*style.seam_color(index), 1.0),
                                action=_seam_select(index)))
        if _section(rows, "Geometry", "edit_pattern"):
            points = model.outline(pattern)
            kinds = {"POLYGON": "Free outline", "RECTANGLE": "Rectangle", "ELLIPSE": "Ellipse"}
            rows.append(Row("field", "Shape", kinds[pattern.shape_kind]))
            rows.append(Row("field", "Points", f"{len(points)} · {sum(p.curve_out for p in points)} curved"))
            rows.append(Row("field", "Mesh", f"{pattern.triangle_count} triangles"))
            rows.append(Row("field", "Meshing", f"{pattern.mesh_time * 1000:.0f} ms"))
            for contour in pattern.contours:
                rows.append(Row("buttons", items=((
                    f"Remove {'hole' if contour.kind == 'HOLE' else 'line'}", "internal",
                    _op("clothnext.pattern_remove_contour", pattern=ident, uid=contour.uid),
                    False, True),)))
            if pattern.symmetry_partner:
                rows.append(Row("buttons", items=(("Unlink Symmetry", "mirror",
                                                   _op("clothnext.pattern_unlink_symmetry"),
                                                   False, True),)))
        if _section(rows, "Arrangement", "arrange"):
            rows.append(Row("buttons", items=(
                ("Front", "arrange", _op("clothnext.pattern_place", side="FRONT"), False, True),
                ("Back", "arrange", _op("clothnext.pattern_place", side="BACK"), False, True),
                ("Reset", "frame", _op("clothnext.pattern_restage"), False, True))))
            rows.append(Row("field", "Canvas X", _length(context, pattern.location[0]),
                            action=_edit("pattern", ident, "location", 0, "Canvas X")))
            rows.append(Row("field", "Canvas Y", _length(context, pattern.location[1]),
                            action=_edit("pattern", ident, "location", 1, "Canvas Y")))
            rows.append(Row("field", "Rotation", f"{pattern.rotation * 57.29578:.1f}°",
                            action=_edit("pattern", ident, "rotation")))
        rows.append(Row("buttons", items=(
            ("Duplicate", "duplicate", _op("clothnext.pattern_duplicate"), False, True),
            ("Mirror", "mirror", _op("clothnext.pattern_mirror"), False, True),
            ("Delete", "", _op("clothnext.pattern_delete"), False, True))))

    @staticmethod
    def patterns(context, rows, current):
        if _section(rows, f"{len(current.patterns)} patterns selected", "pattern"):
            area = sum(p.area for p in current.patterns)
            rows.append(Row("field", "Total area", f"{area * 1e4:.0f} cm²"))
            rows.append(Row("buttons", items=(
                ("Duplicate", "duplicate", _op("clothnext.pattern_duplicate"), False, True),
                ("Mirror", "mirror", _op("clothnext.pattern_mirror"), False, True),
                ("Delete", "", _op("clothnext.pattern_delete"), False, True))))
            rows.append(Row("buttons", items=(
                ("Front", "arrange", _op("clothnext.pattern_place", side="FRONT"), False, True),
                ("Back", "arrange", _op("clothnext.pattern_place", side="BACK"), False, True),
                ("Reset", "frame", _op("clothnext.pattern_restage"), False, True))))

    @staticmethod
    def point(context, rows, current):
        pattern, index = current.pattern, current.point
        points = model.outline(pattern)
        point = points[index] if 0 <= index < len(points) else None
        ui = context.window_manager.cloth_next_pattern_ui
        if _section(rows, f"Point {index + 1} of {len(points)}", "edit_pattern"):
            rows.append(Row("field", "X", _length(context, ui.point_x),
                            action=_edit("ui", "", "point_x")))
            rows.append(Row("field", "Y", _length(context, ui.point_y),
                            action=_edit("ui", "", "point_y")))
            if point is not None:
                ident = pattern.identifier
                rows.append(Row("buttons", items=(
                    ("Corner", "", _op("clothnext.pattern_point_type", pattern=ident, point=index,
                                       smooth=False), not point.smooth, True),
                    ("Smooth", "", _op("clothnext.pattern_point_type", pattern=ident, point=index,
                                       smooth=True), point.smooth, True))))
                rows.append(Row("buttons", items=(("Remove Point", "", _op(
                    "clothnext.pattern_remove_point", pattern=ident, point=index), False,
                    len(points) > 3),)))
        _Context.pattern_summary(context, rows, pattern)

    @staticmethod
    def edge(context, rows, current):
        pattern, index = current.pattern, current.edge
        points = model.outline(pattern)
        curved = points[index % len(points)].curve_out
        ui = context.window_manager.cloth_next_pattern_ui
        ident = pattern.identifier
        if _section(rows, f"Edge {index + 1} of {len(points)}", "edit_curve"):
            rows.append(Row("field", "Length", _length(context, ui.edge_length),
                            action=_edit("ui", "", "edge_length")))
            rows.append(Row("buttons", items=(
                ("Straight", "", _op("clothnext.pattern_segment", pattern=ident, segment=index,
                                     action="STRAIGHT"), not curved, True),
                ("Curved", "", _op("clothnext.pattern_segment", pattern=ident, segment=index,
                                   action="CURVE"), curved, True))))
            rows.append(Row("buttons", items=(("Add Point", "", _op(
                "clothnext.pattern_segment", pattern=ident, segment=index, action="SPLIT", t=0.5),
                False, True),)))
        _Context.pattern_summary(context, rows, pattern)

    @staticmethod
    def pattern_summary(context, rows, pattern):
        if _section(rows, pattern.name, "pattern"):
            rows.append(Row("field", "Particle Dist.", _length(context, pattern.particle_distance),
                            action=_edit("pattern", pattern.identifier, "particle_distance")))
            rows.append(Row("field", "Area", f"{pattern.area * 1e4:.1f} cm²"))

    @staticmethod
    def seam(context, rows, current):
        scene = context.scene
        seam = current.seam
        if _section(rows, "Seam", "sewing"):
            rows.append(Row("field", "Name", seam.name, action=_edit("seam", seam.identifier, "name")))
            pa, pb = model.find(scene, seam.a_pattern), model.find(scene, seam.b_pattern)
            lengths = inspector.seam_lengths(scene, seam)
            for side, pattern, value in (("A", pa, lengths[0] if lengths else None),
                                         ("B", pb, lengths[1] if lengths else None)):
                rows.append(Row("field", f"{side} · {pattern.name if pattern else '—'}",
                                _length(context, value) if value is not None else "—"))
            if lengths:
                diff = abs(lengths[0] - lengths[1])
                warn = diff > 0.003 and diff / max(lengths) > 0.02
                rows.append(Row("field", "Difference", _length(context, diff),
                                color=(*style.WARNING, 1.0) if warn else None))
            rows.append(Row("buttons", items=(
                ("Crossed", "flip", _flip(seam.identifier), seam.flipped, True),
                ("Delete", "", _op("clothnext.pattern_seam_remove", identifier=seam.identifier),
                 False, True))))
            if seam.status:
                rows.append(Row("info", seam.status, color=(*style.ERROR, 1.0)))


def _flip(identifier):
    def run(context):
        for seam in context.scene.cloth_next_pattern_seams:
            if seam.identifier == identifier:
                seam.flipped = not seam.flipped
                bpy.ops.ed.undo_push(message="Flip Seam")
    return run


# -- layout, draw, hit -----------------------------------------------------------------

def _place(context, region, rows):
    box = rect(context, region)
    if box is None:
        return None
    s = style.ui_scale(context)
    x, y, w, h = box
    top = y + h - 6 * s + _state["scroll"]
    for row in rows:
        row.h = (HEADER if row.kind == "header" else ROW + (4 if row.kind == "buttons" else 0)) * s
        top -= row.h
        row.y = top
        row.hits = []
    content = (y + h - 6 * s + _state["scroll"]) - top + 6 * s
    max_scroll = max(0.0, content - h)
    if _state["scroll"] > max_scroll:
        _state["scroll"] = max_scroll
    return box


def draw(context, region):
    rows = build(context)
    box = _place(context, region, rows)
    _state["rows"] = rows
    if box is None:
        return
    pal = style.palette(context)
    s = style.ui_scale(context)
    x, y, w, h = box
    style.rect(x, y, w, h, pal.bar)
    style.rect(x, y, max(1.0, s), h, pal.bar_line)
    pad = 10 * s
    hover = _state["hover"]
    icon_px = 18 if s < 1.25 else (27 if s < 1.75 else 36)
    import gpu
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(int(x), int(y), int(w), int(h))  # region-local pixels
    try:
        for number, row in enumerate(rows):
            if row.y + row.h < y or row.y > y + h:
                continue
            cy = row.y + row.h / 2
            baseline = cy - 4 * s
            hovered = hover == number
            if row.kind == "header":
                if number:
                    style.rect(x + pad, row.y + row.h - 1, w - 2 * pad, max(1.0, s), pal.bar_line)
                chevron = "▾" if row.active else "▸"
                style.text(chevron, x + pad, baseline, pal.text_dim, 10, scale=s)
                if row.icon:
                    style.draw_icon(row.icon, x + pad + 18 * s, cy, icon_px * 0.9, pal.text_dim)
                style.text(style.fit(row.label, w - 60 * s, 11, s), x + pad + 32 * s, baseline,
                           pal.text, 11, scale=s)
                row.hits.append((x, row.y, w, row.h, row.action))
            elif row.kind == "list":
                if row.active:
                    style.rounded(x + 4 * s, row.y + 1, w - 8 * s, row.h - 2, 3 * s, pal.active)
                    style.rect(x + 4 * s, row.y + 3 * s, 2 * s, row.h - 6 * s, (*style.ACCENT, 1.0))
                elif hovered:
                    style.rounded(x + 4 * s, row.y + 1, w - 8 * s, row.h - 2, 3 * s, pal.hover)
                eye = "eye_off" if row.hidden else "eye"
                style.draw_icon(eye, x + pad + 6 * s, cy, icon_px * 0.85,
                                (*pal.text_dim[:3], 0.55 if row.hidden else 0.9))
                color = row.color or (pal.text_dim if row.hidden else pal.text)
                style.text(style.fit(row.label, w - 50 * s, 11, s), x + pad + 22 * s, baseline,
                           color, 11, scale=s)
                row.hits.append((x, row.y, 26 * s, row.h, row.toggle))
                row.hits.append((x + 26 * s, row.y, w - 26 * s, row.h, row.action))
            elif row.kind == "field":
                if hovered and row.action:
                    style.rounded(x + 4 * s, row.y + 1, w - 8 * s, row.h - 2, 3 * s, pal.hover)
                if row.color is not None and row.label.startswith("→"):
                    style.rect(x + pad, cy - 5 * s, 3 * s, 10 * s, row.color)
                style.text(style.fit(row.label, w * 0.48 - pad, 10, s), x + pad + (6 * s if
                           row.color is not None and row.label.startswith("→") else 0),
                           baseline, pal.text_dim, 10, scale=s)
                value_color = row.color if (row.color and not row.label.startswith("→")) \
                    else pal.text
                vx = x + w * 0.48
                if row.action:
                    style.rounded(vx - 4 * s, row.y + 3 * s, w - (vx - x) - pad + 4 * s,
                                  row.h - 6 * s, 3 * s, (*pal.canvas[:3], 0.9))
                style.text(style.fit(row.value, w - (vx - x) - pad - 4 * s, 11, s), vx + 2 * s,
                           baseline, value_color, 11, scale=s)
                if row.action:
                    row.hits.append((x, row.y, w, row.h, row.action))
            elif row.kind == "buttons":
                items = row.items
                gap = 4 * s
                bw = (w - 2 * pad - gap * (len(items) - 1)) / max(1, len(items))
                for k, (label, icon, action, active, enabled) in enumerate(items):
                    bx = x + pad + k * (bw + gap)
                    by, bh = row.y + 3 * s, row.h - 6 * s
                    fill = pal.active if active else (pal.hover if hovered else
                                                      (*pal.text[:3], 0.05))
                    style.rounded(bx, by, bw, bh, 3 * s, fill)
                    if active:
                        style.rect(bx + 4 * s, by, bw - 8 * s, 2 * s, (*style.ACCENT, 1.0))
                    color = pal.text if enabled else (*pal.text[:3], 0.3)
                    tw = style.text_width(label, 10, s)
                    start = bx + (bw - tw - (16 * s if icon else 0)) / 2
                    if icon:
                        style.draw_icon(icon, start + 7 * s, by + bh / 2, icon_px * 0.85, color)
                        start += 16 * s
                    style.text(label, start, by + bh / 2 - 4 * s, color, 10, scale=s)
                    if enabled:
                        row.hits.append((bx, by, bw, bh, action))
            else:
                style.text(style.fit(row.label, w - 2 * pad, 10, s), x + pad, baseline,
                           row.color or pal.text_dim, 10, scale=s)
    finally:
        gpu.state.scissor_test_set(False)


def contains(context, region, px, py):
    box = rect(context, region)
    return box is not None and box[0] <= px <= box[0] + box[2] and box[1] <= py <= box[1] + box[3]


def hover(context, region, px, py):
    rows = _state["rows"] or []
    found = None
    for number, row in enumerate(rows):
        if row.y <= py <= row.y + row.h and row.kind != "info":
            found = number
    changed = found != _state["hover"]
    _state["hover"] = found
    return changed


def click(context, region, px, py):
    for row in _state["rows"] or []:
        for hx, hy, hw, hh, action in row.hits:
            if action is not None and hx <= px <= hx + hw and hy <= py <= hy + hh:
                action(context)
                return True
    return False


def scroll(context, region, direction):
    s = style.ui_scale(context)
    _state["scroll"] = max(0.0, _state["scroll"] + direction * 3 * ROW * s)


def edit_target(context):
    """(owner, property, index, label) for the value popover."""
    edit = _state["edit"]
    if edit is None:
        return None
    kind, identifier, prop, index, label = edit
    scene = context.scene
    if kind == "pattern":
        owner = model.find(scene, identifier)
    elif kind == "seam":
        owner = next((s for s in scene.cloth_next_pattern_seams if s.identifier == identifier), None)
    elif kind == "settings":
        owner = scene.cloth_next_pattern_settings
    else:
        owner = context.window_manager.cloth_next_pattern_ui
    return None if owner is None else (owner, prop, index, label)


class CLOTHNEXT_PT_pattern_value_popover(bpy.types.Panel):
    """Native editing field for one inspector value."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = "Edit Value"
    bl_ui_units_x = 12

    def draw(self, context):
        target = edit_target(context)
        layout = self.layout
        if target is None:
            layout.label(text="Nothing to edit")
            return
        owner, prop, index, label = target
        layout.activate_init = True
        if index >= 0:
            layout.prop(owner, prop, index=index, text=label or prop.replace("_", " ").title())
        else:
            layout.prop(owner, prop)


CLASSES = (CLOTHNEXT_PT_pattern_value_popover,)
