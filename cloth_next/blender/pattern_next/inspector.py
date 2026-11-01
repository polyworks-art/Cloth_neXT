# SPDX-License-Identifier: GPL-3.0-or-later
"""Context of the PATTERN NeXt inspector: what is selected, and edits made
through its numeric fields.

The editor session owns sub-object selection (points, an edge, a seam);
patterns are selected through their panel objects. The inspector mirrors
the selection into window-manager fields (point X/Y, edge length) whose
update callbacks apply the change through the same validated commit path
as the tools. A guard keeps those writes from echoing back as edits.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...pattern_next import editing
from ...pattern_next.curves import segment_length
from ...pattern_next.shapes import Contour, to_canvas, to_local
from . import model, sync

_guard = {"active": False}


@dataclass(frozen=True)
class Selection:
    kind: str                 # NONE, PATTERNS, PATTERN, POINT, EDGE, SEAM
    pattern: object = None
    patterns: tuple = ()
    point: int = -1
    edge: int = -1
    seam: object = None


def _session(context):
    from . import editor
    return editor.session_for(getattr(context, "window", None))


def selection(context):
    scene = context.scene
    from .draw import is_selected
    chosen = tuple(p for p in scene.cloth_next_patterns if is_selected(p))
    session = _session(context)
    tool = context.window_manager.cloth_next_pattern_ui.tool
    seams = scene.cloth_next_pattern_seams
    index = scene.cloth_next_pattern_settings.seam_index
    if tool == "SEWING" and 0 <= index < len(seams):
        return Selection("SEAM", seam=seams[index], patterns=chosen)
    if session is not None and tool in {"EDIT", "CURVE"}:
        for identifier, points in session.points_selected.items():
            pattern = model.find(scene, identifier)
            if pattern is not None and len(points) == 1:
                return Selection("POINT", pattern=pattern, patterns=chosen,
                                 point=next(iter(points)))
        if session.selected_segment is not None:
            pattern = model.find(scene, session.selected_segment[0])
            if pattern is not None:
                return Selection("EDGE", pattern=pattern, patterns=chosen,
                                 edge=session.selected_segment[1])
    active = model.for_object(scene, context.view_layer.objects.active)
    if active is not None and active in chosen and len(chosen) == 1:
        return Selection("PATTERN", pattern=active, patterns=chosen)
    if len(chosen) == 1:
        return Selection("PATTERN", pattern=chosen[0], patterns=chosen)
    if chosen:
        return Selection("PATTERNS", pattern=active if active in chosen else chosen[0],
                         patterns=chosen)
    return Selection("NONE")


def refresh(context):
    """Copy the current point / edge values into the inspector fields."""
    ui = context.window_manager.cloth_next_pattern_ui
    current = selection(context)
    _guard["active"] = True
    try:
        if current.kind == "POINT":
            points = model.outline(current.pattern)
            if 0 <= current.point < len(points):
                location, rotation = model.placement(current.pattern)
                x, y = to_canvas(points[current.point].co, location, rotation)
                ui.point_x, ui.point_y = x, y
        elif current.kind == "EDGE":
            points = model.outline(current.pattern)
            if 0 <= current.edge < len(points):
                ui.edge_length = segment_length(Contour(points), current.edge)
    finally:
        _guard["active"] = False


def apply_point(context):
    if _guard["active"]:
        return
    current = selection(context)
    if current.kind != "POINT":
        return
    ui = context.window_manager.cloth_next_pattern_ui
    pattern = current.pattern
    location, rotation = model.placement(pattern)
    local = to_local((ui.point_x, ui.point_y), location, rotation)
    points = editing.move_point(model.outline(pattern), current.point, local)
    _commit(context, pattern, points, "Move Point")


def apply_edge_length(context):
    if _guard["active"]:
        return
    current = selection(context)
    if current.kind != "EDGE":
        return
    ui = context.window_manager.cloth_next_pattern_ui
    try:
        points = editing.set_segment_length(model.outline(current.pattern), current.edge,
                                            ui.edge_length)
    except ValueError:
        return
    _commit(context, current.pattern, points, "Set Edge Length")


def _commit(context, pattern, points, label):
    model.make_free_form(pattern)
    ok, message = sync.commit(context.scene, pattern, points)
    session = _session(context)
    if session is not None:
        session.say(f"{label}: {pattern.triangle_count} triangles" if ok
                    else f"Edit rejected: {message}", not ok)
    if ok:
        from .operators import sync_symmetry
        sync_symmetry(context.scene, pattern)
        # Window-manager fields are not undo data; record the pattern edit.
        try:
            import bpy
            bpy.ops.ed.undo_push(message=label)
        except RuntimeError:
            pass
    refresh(context)
    from . import draw
    draw.tag_redraw()


def select_from_list(context):
    """Clicking a row of the pattern list selects that pattern."""
    if _guard["active"]:
        return
    scene = context.scene
    index = scene.cloth_next_pattern_settings.pattern_index
    if not 0 <= index < len(scene.cloth_next_patterns):
        return
    from .editor import select_pattern
    pattern = scene.cloth_next_patterns[index]
    if pattern.mesh_object is None:
        sync.ensure_all(scene)
    select_pattern(context, pattern)
    from . import draw
    draw.tag_redraw()


def follow_selection(context):
    """Keep the list's active row on the active pattern (no selection echo)."""
    scene = context.scene
    active = model.for_object(scene, context.view_layer.objects.active)
    if active is None:
        return
    index = model.index_of(scene, active)
    settings = scene.cloth_next_pattern_settings
    if index >= 0 and settings.pattern_index != index:
        _guard["active"] = True
        try:
            settings.pattern_index = index
        finally:
            _guard["active"] = False


def seams_of(scene, pattern):
    return [(i, s) for i, s in enumerate(scene.cloth_next_pattern_seams)
            if pattern.identifier in (s.a_pattern, s.b_pattern)]


def dimensions(pattern):
    ring = model.canvas_outline(pattern, 0.002)
    if not ring:
        return 0.0, 0.0
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return max(xs) - min(xs), max(ys) - min(ys)


def seam_lengths(scene, seam):
    from .draw import line_length, seam_sides
    sides = seam_sides(scene, seam)
    if sides is None:
        return None
    return line_length(sides[0]), line_length(sides[1])

