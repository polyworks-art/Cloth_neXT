# SPDX-License-Identifier: GPL-3.0-or-later
"""The 2D Pattern editor session (one modal operator per window).

Follows the Cloth NeXt modal-editor convention (see ``sewing.py``): the
session starts automatically while a window shows the PATTERN NeXt
workspace, owns input only inside the locked 2D view, and passes
everything else through, so the left 3D view keeps Blender's own tools,
navigation and shortcuts. Undo/redo pass through (the session holds pattern
identifiers, never RNA pointers, across events). Every completed edit is
one undo step.

Shortcuts inside the 2D editor (checked against Blender's 3D View keymap;
these keys are only consumed there): Q Select, H Polygon, S Rectangle,
E Ellipse, Z Edit Pattern, V Edit Curve, G Internal, M Sewing (N stays
Blender's sidebar), Enter finish, Backspace remove last point, Esc cancel,
Shift angle steps / square, Ctrl temporarily disables snapping, R rotate,
Shift+D duplicate, Ctrl+C / Ctrl+V copy and paste, X / Delete delete,
Home frame all, Numpad . / F frame selected, F flip seam (Sewing).
"""

from __future__ import annotations

from dataclasses import replace
import logging
import math
import time

import bpy


from ...pattern_next import editing
from ...pattern_next.curves import display_polyline
from ...pattern_next.seams import SeamSide, side_polyline
from ...pattern_next.shapes import (Contour, ContourPoint, add, distance, rectangle_points,
                                    ellipse_points, signed_area, sub, to_canvas, to_local,
                                    vector_to_local)
from ...pattern_next.snapping import snap
from ...pattern_next.validation import PatternError, validate_ring
from . import dock, draw, model, style, sync, toolbar, workspace

_log = logging.getLogger(__name__)

_sessions = {}

TOOL_KEYS = {"Q": "SELECT", "H": "POLYGON", "S": "RECTANGLE", "E": "ELLIPSE", "Z": "EDIT",
             "C": "CURVE_DRAW",
             "V": "CURVE", "G": "INTERNAL", "M": "SEWING"}
NUMBER_KEYS = ("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE")
NAVIGATION = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
              "TRACKPADPAN", "TRACKPADZOOM", "MOUSESMARTZOOM", "TIMER", "TIMER_REPORT",
              "TIMERREGION", "TIMER0", "TIMER1", "TIMER2", "TIMERJOBS", "TIMERAUTOSAVE",
              "WINDOW_DEACTIVATE", "NONE"}
PICK_PX = 9.0
CLOSE_PX = 11.0
LIVE_MESH_BUDGET = 0.035  # remesh while dragging when a rebuild is this fast

_clipboard = []
_context_target = {}


def session_for(window):
    if window is None:
        return None
    try:
        op = _sessions.get(window.as_pointer())
    except ReferenceError:
        return None
    return op if op is not None and not op.finished else None


def _undo(message):
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        _log.debug("undo push skipped: %s", message)


def select_pattern(context, pattern, extend=False, toggle=False):
    """Selection is the panel object's selection: both editors agree."""
    view_layer = context.view_layer
    if not extend and not toggle:
        # Not context.selected_objects: inside the 2D editor every object
        # type is hidden, so that list is empty there.
        for obj in view_layer.objects:
            try:
                if obj.select_get():
                    obj.select_set(False)
            except RuntimeError:
                pass
    if pattern is None:
        return
    obj = pattern.mesh_object
    if obj is None:
        return
    try:
        state = not obj.select_get() if toggle else True
        obj.select_set(state)
        if state:
            view_layer.objects.active = obj
    except RuntimeError:
        pass


def selected_patterns(scene):
    return [p for p in scene.cloth_next_patterns if draw.is_selected(p)]


def frame(context, region, rv3d, patterns, margin=1.18):
    rings = []
    for pattern in patterns:
        rings.extend(model.canvas_rings(pattern, 0.002))
    points = [p for ring in rings for p in ring]
    if not points:
        rv3d.view_location = (0.0, 0.0, 0.6)
        rv3d.view_distance = 2.2
        return
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    width, height = max(max(xs) - min(xs), 0.05), max(max(ys) - min(ys), 0.05)
    # Fit into the visible canvas: not under an overlapping sidebar, the
    # tool strip or the status strip.
    sidebar = 0
    for area in getattr(context.window, "screen", None).areas if context.window else ():
        if any(r.as_pointer() == region.as_pointer() for r in area.regions):
            sidebar = sum(r.width for r in area.regions if r.type == "UI" and r.width > 1
                          and region.x < r.x < region.x + region.width)
    s = style.ui_scale(context)
    if context.scene.cloth_next_pattern_settings.show_inspector:
        sidebar += dock.width(context, region)
    usable_w = max(region.width - sidebar - 60 * s, 50)
    usable_h = max(region.height - (style.BAR + style.STATUS) * s - 60 * s, 50)
    for _ in range(3):
        rv3d.view_location = (cx, 0.0, cy)
        current = draw.pixel_size(region, rv3d)
        needed = max(width / usable_w, height / usable_h)
        rv3d.view_distance *= needed * margin / current
        rv3d.update()
    px = draw.pixel_size(region, rv3d)
    shift_y = (style.BAR - style.STATUS) * s / 2 * px
    rv3d.view_location = (cx + sidebar / 2 * px, 0.0, cy + shift_y)


class CLOTHNEXT_OT_pattern_editor(bpy.types.Operator):
    bl_idname = "clothnext.pattern_editor"
    bl_label = "PATTERN NeXt 2D Editor"
    bl_description = "Draw and edit patterns in the 2D Pattern editor"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        window = context.window
        if window is None or session_for(window) is not None:
            return {"CANCELLED"}
        if not workspace.window_pattern_areas(window, repair=True):
            return {"CANCELLED"}
        self.window = window
        self.key = window.as_pointer()
        self.finished = False
        self.reset_tool_state()
        self.toolbar_hover = None
        self.message, self.message_error = "", False
        self.mouse_px = None
        self.region_key = None
        self.cursor = None
        self.snapped = None
        self.last_live = 0.0
        self.last_press = (0.0, (0.0, 0.0))
        self.double = False
        _sessions[self.key] = self
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # -- state ------------------------------------------------------------------
    def reset_tool_state(self):
        self.draft = []
        self.draft_pattern = None
        self.drag = None
        self.hover = None
        self.hover_seam = None
        self.seam_first = None
        self.points_selected = {}
        self.selected_segment = None
        self.preview = None

    def say(self, message, error=False):
        self.message, self.message_error = message, error
        if error:
            _log.info("PATTERN NeXt: %s", message)

    def finish(self):
        if self.finished:
            return
        self.finished = True
        _sessions.pop(self.key, None)
        try:
            self.window.cursor_modal_restore()
        except ReferenceError:
            pass
        draw.tag_redraw()

    def cancel(self, context):
        self.finish()

    def hidden_patterns(self):
        return set()

    def hover_pattern(self):
        return self.hover.get("pattern") if self.hover else None

    # -- helpers -------------------------------------------------------------------
    def _alive(self, context):
        try:
            return (not self.finished and context.window == self.window
                    and workspace.is_pattern_workspace(self.window.workspace)
                    and context.mode == "OBJECT")
        except (ReferenceError, AttributeError):
            return False

    def _locate(self, event):
        for area in workspace.window_pattern_areas(self.window):
            region = workspace.window_region(area)
            if region is None:
                continue
            x, y = event.mouse_x - region.x, event.mouse_y - region.y
            if 0 <= x < region.width and 0 <= y < region.height:
                for other in area.regions:
                    if other.type in {"UI", "HEADER", "TOOL_HEADER", "TOOLS", "ASSET_SHELF"} \
                            and other.width > 1 and other.height > 1 \
                            and other.x <= event.mouse_x < other.x + other.width \
                            and other.y <= event.mouse_y < other.y + other.height:
                        return area, region, None
                return area, region, (x, y)
        return None

    def _region_by_key(self):
        for area in workspace.window_pattern_areas(self.window):
            region = workspace.window_region(area)
            if region is not None and region.as_pointer() == self.region_key:
                return area, region
        return None

    def _redraw(self):
        draw.tag_redraw()

    def _chrome(self, context, region, mouse):
        """Toolbar button, tool strip or status strip under the mouse."""
        found = toolbar.hit(context, region, *mouse,
                            has_selection=bool(selected_patterns(context.scene)))
        if found is not None:
            return found
        if dock.contains(context, region, *mouse):
            return _DOCK
        x, y, w, h = toolbar.status_rect(context, region)
        if x <= mouse[0] <= x + w and y <= mouse[1] <= y + h:
            return toolbar.Strip(x, y, w, h, (), ())
        return None

    def _px(self, region, rv3d):
        return draw.pixel_size(region, rv3d)

    def _snap(self, context, region, rv3d, canvas, event, origin=None, exclude=None):
        settings = context.scene.cloth_next_pattern_settings
        radius = PICK_PX * draw.ui_scale(context) * self._px(region, rv3d)
        use = settings.use_snap and not event.ctrl
        vertices, polylines = [], []
        if use and (settings.snap_vertex or settings.snap_edge):
            for pattern in context.scene.cloth_next_patterns:
                points = model.outline(pattern)
                for i, p in enumerate(points):
                    if exclude and (pattern.identifier, i) in exclude:
                        continue
                    vertices.append((sync.canvas_of(pattern, p.co), (pattern.identifier, i)))
                if settings.snap_edge and not (exclude and any(e[0] == pattern.identifier
                                                               for e in exclude)):
                    polylines.append((model.canvas_outline(pattern, 0.001), True,
                                      pattern.identifier))
        for p in self.draft:
            vertices.append((p.co, ("draft", 0)))
        result = snap(canvas, vertices=vertices if settings.snap_vertex else (),
                      polylines=polylines, radius=radius,
                      grid=draw.grid_step(context, region, rv3d), origin=origin,
                      constrain=event.shift and origin is not None,
                      use_vertices=use and settings.snap_vertex,
                      use_edges=use and settings.snap_edge,
                      use_grid=use and settings.snap_grid)
        self.snapped = result
        return result.co

    def _pattern_at(self, context, canvas):
        found = None
        for pattern in draw.visible_patterns(context.scene):
            if editing.ring_contains(model.canvas_rings(pattern, 0.002), canvas):
                found = pattern  # last drawn = topmost
        return found

    def _local(self, pattern, canvas):
        location, rotation = model.placement(pattern)
        return to_local(canvas, location, rotation)

    def _pick_point(self, context, canvas, radius, prefer=None):
        best = None
        patterns = draw.visible_patterns(context.scene)
        if prefer is not None:
            patterns.sort(key=lambda p: p.identifier != prefer)
        for pattern in patterns:
            local = self._local(pattern, canvas)
            hit = editing.nearest_point(model.outline(pattern), local, radius)
            if hit and (best is None or hit[0] < best[0] - 1e-12):
                best = (hit[0], pattern.identifier, hit[1])
        return best

    def _pick_segment(self, context, canvas, radius):
        best = None
        for pattern in draw.visible_patterns(context.scene):
            local = self._local(pattern, canvas)
            hit = editing.nearest_segment(model.outline(pattern), local, radius)
            if hit and (best is None or hit[0] < best[0]):
                best = (hit[0], pattern.identifier, hit[1], hit[2])
        return best

    def _pick_handle(self, context, canvas, radius):
        best = None
        for pattern in context.scene.cloth_next_patterns:
            if not (draw.is_selected(pattern) or self.hover_pattern() == pattern.identifier):
                continue
            local = self._local(pattern, canvas)
            hit = editing.nearest_handle(model.outline(pattern), local, radius)
            if hit and (best is None or hit[0] < best[0]):
                best = (hit[0], pattern.identifier, hit[1], hit[2])
        return best

    def _pick_seam(self, context, canvas, radius):
        best = None
        for seam in context.scene.cloth_next_pattern_seams:
            sides = draw.seam_sides(context.scene, seam)
            if sides is None:
                continue
            for line in sides:
                for p, q in zip(line, line[1:]):
                    d = editing.nearest_segment(
                        (ContourPoint(0, p), ContourPoint(1, q)), canvas, radius, closed=False)
                    if d and (best is None or d[0] < best[0]):
                        best = (d[0], seam.identifier)
        return best[1] if best else None

    # -- modal ------------------------------------------------------------------------
    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except ReferenceError:
            self.drag = None
            return {"PASS_THROUGH"}
        except Exception as exc:  # never leave a broken handler behind
            _log.exception("PATTERN NeXt editor event failed")
            self.say(f"Internal error: {exc}", True)
            self.drag = None
            return {"PASS_THROUGH"}

    def _modal(self, context, event):
        if self.finished:
            return {"FINISHED"}
        if not self._alive(context):
            self.finish()
            return {"FINISHED", "PASS_THROUGH"}
        if event.type in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            over = self._locate(event)
            if over is not None and over[2] is not None and \
                    dock.contains(context, over[1], *over[2]):
                dock.scroll(context, over[1], -1 if event.type == "WHEELUPMOUSE" else 1)
                self._redraw()
                return {"RUNNING_MODAL"}
        if event.type in NAVIGATION or event.type.startswith("NDOF"):
            return {"PASS_THROUGH"}
        located = self._locate(event)
        busy = bool(self.draft) or self.drag is not None
        if located is None or located[2] is None:
            if busy and event.type in {"MOUSEMOVE", "LEFTMOUSE"} and self.drag is not None:
                found = self._region_by_key()
                if found is not None:
                    area, region = found
                    located = (area, region, (event.mouse_x - region.x, event.mouse_y - region.y))
            if located is None or located[2] is None:
                if self.hover or self.toolbar_hover:
                    self.hover = self.toolbar_hover = None
                    self._redraw()
                if event.type == "ESC" and event.value == "PRESS" and busy:
                    self._escape()
                    return {"RUNNING_MODAL"}
                return {"PASS_THROUGH"}
        area, region, mouse = located
        rv3d = area.spaces.active.region_3d
        self.region_key = region.as_pointer()
        self.mouse_px = mouse
        canvas = draw.region_to_canvas(region, rv3d, mouse)
        self.cursor = canvas
        tool = context.window_manager.cloth_next_pattern_ui.tool

        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            if event.type == "INBETWEEN_MOUSEMOVE":
                return {"RUNNING_MODAL"}
            chrome = None if self.drag else self._chrome(context, region, mouse)
            hovered = getattr(chrome, "identifier", None)
            if hovered != self.toolbar_hover or chrome is not None:
                self.toolbar_hover = hovered
            self.window.cursor_modal_set("DEFAULT" if chrome is not None else "CROSSHAIR")
            if chrome is None:
                self._move(context, region, rv3d, canvas, event, tool)
            else:
                self.hover = None
                self.snapped = None
                if chrome is _DOCK:
                    dock.hover(context, region, *mouse)
            if chrome is not _DOCK and dock._state["hover"] is not None:
                dock._state["hover"] = None
            self._redraw()
            return {"RUNNING_MODAL"}

        if event.type in {"LEFTMOUSE", "RIGHTMOUSE"} and self.drag is None:
            chrome = self._chrome(context, region, mouse)
            if chrome is not None:
                if event.type == "LEFTMOUSE" and event.value == "PRESS" and chrome is _DOCK:
                    dock.click(context, region, *mouse)
                    self._selection_changed(context)
                    self._redraw()
                elif event.type == "LEFTMOUSE" and event.value == "PRESS" and \
                        hasattr(chrome, "identifier") and chrome.enabled:
                    self._button(context, region, rv3d, chrome.identifier)
                    self._redraw()
                return {"RUNNING_MODAL"}

        # Undo/redo: drop transient drags, keep the draft, let Blender undo.
        if event.ctrl and event.type in {"Z", "Y"}:
            if self.drag is not None:
                self._cancel_drag(context)
            self.hover = None
            return {"PASS_THROUGH"}
        if event.type in {"N", "T", "F3", "F4", "F11", "F12"} or (event.ctrl and event.type in {
                "S", "O", "N", "Q", "SPACE"}) or event.oskey or event.type.startswith("F") and \
                event.type[1:].isdigit():
            return {"PASS_THROUGH"}
        if event.type in {"TAB", "NUMPAD_0", "NUMPAD_1", "NUMPAD_3", "NUMPAD_7", "NUMPAD_5",
                          "NUMPAD_2", "NUMPAD_4", "NUMPAD_6", "NUMPAD_8", "NUMPAD_9", "ACCENT_GRAVE"}:
            return {"RUNNING_MODAL"}  # orientation keys are inert in the 2D editor

        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            # Blender reports the second click as DOUBLE_CLICK; simulated or
            # remote input may not, so a quick second press counts too.
            now = time.perf_counter()
            last_time, last_px = self.last_press
            self.double = event.value == "DOUBLE_CLICK" or (
                now - last_time < 0.4 and distance(last_px, mouse) < 5 * draw.ui_scale(context))
            self.last_press = (0.0, mouse) if self.double else (now, mouse)
        elif event.type == "LEFTMOUSE":
            pass  # keep the flag through the matching release
        else:
            self.double = False
        handled = self._event(context, area, region, rv3d, canvas, event, tool)
        if handled:
            self._redraw()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}

    # -- event dispatch ---------------------------------------------------------------
    def _event(self, context, area, region, rv3d, canvas, event, tool):
        ui = context.window_manager.cloth_next_pattern_ui
        value, kind = event.value, event.type
        idle = not self.draft and self.drag is None
        if value == "PRESS" and idle and not (event.ctrl or event.alt or event.shift):
            if kind in TOOL_KEYS and not (tool == "SEWING" and kind == "F"):
                if kind == "S" and tool == "SELECT" and selected_patterns(context.scene):
                    pass
                else:
                    ui.tool = TOOL_KEYS[kind]
                    self.reset_tool_state()
                    return True
            if kind in NUMBER_KEYS:
                ui.tool = toolbar.TOOL_ROWS_FLAT[NUMBER_KEYS.index(kind)]
                self.reset_tool_state()
                return True
        if value == "PRESS" and kind == "I" and idle and not (event.ctrl or event.alt):
            settings = context.scene.cloth_next_pattern_settings
            settings.show_inspector = not settings.show_inspector
            return True
        if value == "PRESS" and kind == "HOME":
            frame(context, region, rv3d, list(context.scene.cloth_next_patterns))
            return True
        if value == "PRESS" and kind in {"NUMPAD_PERIOD", "F"} and not (tool == "SEWING" and kind == "F"):
            chosen = selected_patterns(context.scene) or list(context.scene.cloth_next_patterns)
            frame(context, region, rv3d, chosen)
            return True
        if value == "PRESS" and kind == "ESC":
            return self._escape()
        if kind == "RIGHTMOUSE" and value == "PRESS":
            if self.draft and tool in {"POLYGON", "CURVE_DRAW", "INTERNAL"}:
                return self._finish_draft(context, tool)
            if self.drag is not None:
                self._cancel_drag(context)
                return True
            return self._context_menu(context, canvas, region, rv3d, tool)
        handler = getattr(self, "_tool_" + tool.lower(), None)
        if handler is not None and handler(context, region, rv3d, canvas, event):
            return True
        if value == "PRESS" and idle:
            return self._common_keys(context, region, rv3d, canvas, event)
        return False

    def _common_keys(self, context, region, rv3d, canvas, event):
        kind = event.type
        scene = context.scene
        if kind in {"X", "DEL"} and not event.ctrl:
            if selected_patterns(scene):
                bpy.ops.clothnext.pattern_delete()
                return True
        if kind == "D" and event.shift:
            if bpy.ops.clothnext.pattern_duplicate() == {"FINISHED"}:
                self._start_move(context, canvas)
            return True
        if kind == "C" and event.ctrl:
            bpy.ops.clothnext.pattern_copy()
            return True
        if kind == "V" and event.ctrl:
            bpy.ops.clothnext.pattern_paste(x=canvas[0], y=canvas[1])
            return True
        if kind == "A" and not event.ctrl:
            for pattern in scene.cloth_next_patterns:
                select_pattern(context, pattern, extend=True) if not event.alt else None
            if event.alt:
                select_pattern(context, None)
            return True
        return False

    def _escape(self):
        if self.drag is not None:
            self._cancel_drag(bpy.context)
            return True
        if self.draft or self.seam_first is not None or self.message:
            self.draft, self.draft_pattern, self.seam_first = [], None, None
            self.message = ""
            return True
        return True

    def _button(self, context, region, rv3d, identifier):
        ui = context.window_manager.cloth_next_pattern_ui
        settings = context.scene.cloth_next_pattern_settings
        if identifier.startswith("tool:"):
            ui.tool = identifier[5:]
            self.reset_tool_state()
        elif identifier.startswith("toggle:"):
            name = identifier[7:]
            setattr(settings, name, not getattr(settings, name))
        elif identifier == "action:frame":
            frame(context, region, rv3d, draw.visible_patterns(context.scene))
        elif identifier == "action:mirror":
            bpy.ops.clothnext.pattern_mirror("EXEC_DEFAULT", linked=True)
        elif identifier == "action:duplicate":
            bpy.ops.clothnext.pattern_duplicate("EXEC_DEFAULT")
        elif identifier == "menu:display":
            bpy.ops.wm.call_panel(name="CLOTHNEXT_PT_pattern_display_popover", keep_open=True)

    def _context_menu(self, context, canvas, region, rv3d, tool):
        radius = PICK_PX * draw.ui_scale(context) * self._px(region, rv3d)
        _context_target.clear()
        segment = self._pick_segment(context, canvas, radius)
        point = self._pick_point(context, canvas, radius)
        pattern = self._pattern_at(context, canvas)
        if point:
            _context_target.update(pattern=point[1], point=point[2])
        if segment:
            _context_target.update(pattern=segment[1], segment=segment[2], t=segment[3])
        elif pattern is not None:
            _context_target.update(pattern=pattern.identifier)
            if not draw.is_selected(pattern):
                select_pattern(context, pattern)
        seam = self._pick_seam(context, canvas, radius) if tool == "SEWING" else None
        if seam:
            _context_target["seam"] = seam
        bpy.ops.wm.call_menu(name="CLOTHNEXT_MT_pattern_context")
        return True

    # -- shared drag handling -------------------------------------------------------------
    def _move(self, context, region, rv3d, canvas, event, tool):
        if self.drag is not None:
            self._drag_update(context, region, rv3d, canvas, event)
            return
        radius = PICK_PX * draw.ui_scale(context) * self._px(region, rv3d)
        self.hover = None
        self.hover_seam = None
        if tool in {"POLYGON", "CURVE_DRAW", "INTERNAL"} and self.draft:
            origin = self.draft[-1].co
            self._snap(context, region, rv3d, canvas, event, origin=origin)
            return
        if tool in {"POLYGON", "CURVE_DRAW", "RECTANGLE", "ELLIPSE", "INTERNAL"}:
            self._snap(context, region, rv3d, canvas, event)
            pattern = self._pattern_at(context, canvas)
            if pattern is not None and tool == "INTERNAL":
                self.hover = {"kind": "pattern", "pattern": pattern.identifier}
            return
        self.snapped = None
        if tool == "SEWING":
            self.hover_seam = self._pick_seam(context, canvas, radius * 0.8)
            segment = self._pick_segment(context, canvas, radius)
            if segment:
                self.hover = {"kind": "segment", "pattern": segment[1], "index": segment[2],
                              "t": segment[3]}
            return
        if tool == "CURVE":
            handle = self._pick_handle(context, canvas, radius)
            if handle:
                self.hover = {"kind": "handle", "pattern": handle[1], "index": handle[2],
                              "side": handle[3]}
                return
        if tool in {"EDIT", "CURVE"}:
            point = self._pick_point(context, canvas, radius)
            if point:
                self.hover = {"kind": "point", "pattern": point[1], "index": point[2]}
                return
            segment = self._pick_segment(context, canvas, radius)
            if segment:
                self.hover = {"kind": "segment", "pattern": segment[1], "index": segment[2],
                              "t": segment[3]}
                return
        pattern = self._pattern_at(context, canvas)
        if pattern is not None:
            self.hover = {"kind": "pattern", "pattern": pattern.identifier}

    def _selection_changed(self, context):
        from . import inspector
        inspector.follow_selection(context)
        inspector.refresh(context)
        for area in self.window.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "UI":
                        region.tag_redraw()

    def _start_move(self, context, canvas):
        chosen = selected_patterns(context.scene)
        if not chosen:
            return
        self.drag = {"kind": "move", "start": canvas, "moved": False,
                     "orig": {p.identifier: tuple(p.location) for p in chosen}}

    def _drag_update(self, context, region, rv3d, canvas, event):
        drag = self.drag
        scene = context.scene
        kind = drag["kind"]
        if kind == "move":
            delta = sub(canvas, drag["start"])
            if not event.ctrl and scene.cloth_next_pattern_settings.snap_grid:
                step = draw.grid_step(context, region, rv3d)
                delta = (round(delta[0] / step) * step, round(delta[1] / step) * step)
            if event.shift:  # axis lock
                delta = (delta[0], 0.0) if abs(delta[0]) >= abs(delta[1]) else (0.0, delta[1])
            drag["moved"] = drag["moved"] or distance(canvas, drag["start"]) > 0
            for identifier, origin in drag["orig"].items():
                pattern = model.find(scene, identifier)
                if pattern is not None:
                    pattern.location = add(origin, delta)
        elif kind == "rotate":
            center = drag["center"]
            angle = math.atan2(canvas[1] - center[1], canvas[0] - center[0]) - drag["angle0"]
            if event.shift:
                angle = round(angle / math.radians(15)) * math.radians(15)
            drag["delta"] = angle
            for identifier, (location, rotation) in drag["orig"].items():
                pattern = model.find(scene, identifier)
                if pattern is None:
                    continue
                c, s = math.cos(angle), math.sin(angle)
                rel = sub(location, center)
                pattern.location = add(center, (rel[0] * c - rel[1] * s, rel[0] * s + rel[1] * c))
                pattern.rotation = rotation + angle
        elif kind == "box":
            drag["end"] = canvas
        elif kind in {"rect", "ellipse"}:
            current = self._snap(context, region, rv3d, canvas, event if not event.shift else
                                 _NoShift(event))
            if event.shift:
                dx, dy = sub(current, drag["anchor"])
                size = max(abs(dx), abs(dy))
                current = add(drag["anchor"], (math.copysign(size, dx or 1), math.copysign(size, dy or 1)))
            drag["current"] = current
        elif kind == "poly_point":
            px = draw.canvas_to_region(region, rv3d, canvas)
            start_px = drag["start_px"]
            if px and distance(px, start_px) > 5 * draw.ui_scale(context):
                drag["handle"] = sub(canvas, drag["co"])
        elif kind in {"points", "segment", "bend", "handle"}:
            self._edit_drag(context, region, rv3d, canvas, event)

    def _finish_drag(self, context, region, rv3d, canvas, event):
        drag, self.drag = self.drag, None
        scene = context.scene
        kind = drag["kind"]
        if kind == "move":
            if drag["moved"]:
                _undo("Move Pattern")
        elif kind == "rotate":
            _undo("Rotate Pattern")
        elif kind == "box":
            end = drag.get("end", drag["start"])
            x0, x1 = sorted((drag["start"][0], end[0]))
            y0, y1 = sorted((drag["start"][1], end[1]))
            if not event.shift:
                select_pattern(context, None)
            for pattern in scene.cloth_next_patterns:
                ring = model.canvas_outline(pattern, 0.002)
                if ring and all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in ring):
                    select_pattern(context, pattern, extend=True)
        elif kind in {"rect", "ellipse"}:
            self._create_primitive(context, region, rv3d, drag)
        elif kind == "poly_point":
            self._add_draft_point(context, drag)
        elif kind in {"points", "segment", "bend", "handle"}:
            self._edit_release(context, drag)

    def _cancel_drag(self, context):
        drag, self.drag = self.drag, None
        if drag is None:
            return
        scene = context.scene
        if drag["kind"] in {"move", "rotate"}:
            for identifier, origin in drag["orig"].items():
                pattern = model.find(scene, identifier)
                if pattern is None:
                    continue
                if drag["kind"] == "move":
                    pattern.location = origin
                else:
                    pattern.location, pattern.rotation = origin
        elif drag["kind"] in {"points", "segment", "bend", "handle"}:
            pattern = model.find(scene, drag["pattern"])
            if pattern is not None and drag.get("live"):
                sync.commit(scene, pattern, drag["orig"])
        self.preview = None

    # -- Select tool -------------------------------------------------------------------------
    def _tool_select(self, context, region, rv3d, canvas, event):
        if event.type == "LEFTMOUSE":
            if event.value in {"PRESS", "DOUBLE_CLICK"}:
                if self.drag and self.drag["kind"] == "rotate":
                    self.drag = None
                    _undo("Rotate Pattern")
                    return True
                pattern = self._pattern_at(context, canvas)
                if pattern is None:
                    self.drag = {"kind": "box", "start": canvas, "end": canvas}
                    return True
                if event.shift:
                    select_pattern(context, pattern, toggle=True)
                elif not draw.is_selected(pattern):
                    select_pattern(context, pattern)
                else:
                    select_pattern(context, pattern, extend=True)
                self._start_move(context, canvas)
                return True
            if event.value == "RELEASE" and self.drag is not None and self.drag["kind"] != "rotate":
                self._finish_drag(context, region, rv3d, canvas, event)
                self._selection_changed(context)
                return True
        if event.value == "PRESS" and event.type == "R" and self.drag is None:
            chosen = selected_patterns(context.scene)
            if chosen:
                rings = [p for pattern in chosen for p in model.canvas_outline(pattern, 0.002)]
                center = (sum(p[0] for p in rings) / len(rings), sum(p[1] for p in rings) / len(rings))
                self.drag = {"kind": "rotate", "center": center,
                             "angle0": math.atan2(canvas[1] - center[1], canvas[0] - center[0]),
                             "orig": {p.identifier: (tuple(p.location), float(p.rotation))
                                      for p in chosen}}
                self.say("Rotate: move the mouse, click to confirm, Shift for 15° steps, Esc cancels")
            return True
        if event.value == "PRESS" and event.type in {"RET", "NUMPAD_ENTER"} and self.drag \
                and self.drag["kind"] == "rotate":
            self.drag = None
            self.message = ""
            _undo("Rotate Pattern")
            return True
        return False

    # -- Polygon / Internal drafting ---------------------------------------------------------
    def _tool_polygon(self, context, region, rv3d, canvas, event):
        return self._draft_tool(context, region, rv3d, canvas, event, "POLYGON")

    def _tool_curve_draw(self, context, region, rv3d, canvas, event):
        return self._draft_tool(context, region, rv3d, canvas, event, "CURVE_DRAW")

    def _tool_internal(self, context, region, rv3d, canvas, event):
        if event.type == "L" and event.value == "PRESS" and not event.ctrl:
            ui = context.window_manager.cloth_next_pattern_ui
            ui.internal_kind = "LINE" if ui.internal_kind == "HOLE" else "HOLE"
            return True
        return self._draft_tool(context, region, rv3d, canvas, event, "INTERNAL")

    def _draft_tool(self, context, region, rv3d, canvas, event, tool):
        line_mode = (tool == "INTERNAL" and
                     context.window_manager.cloth_next_pattern_ui.internal_kind == "LINE")
        if event.type == "LEFTMOUSE" and self.double and line_mode and len(self.draft) >= 2:
            return self._finish_draft(context, tool)  # double-click ends an internal line
        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            if tool == "INTERNAL" and not self.draft:
                pattern = self._pattern_at(context, canvas)
                if pattern is None:
                    self.say("Internal shapes start inside a pattern", True)
                    return True
                self.draft_pattern = pattern.identifier
            origin = self.draft[-1].co if self.draft else None
            co = self._snap(context, region, rv3d, canvas, event, origin=origin)
            start_px = draw.canvas_to_region(region, rv3d, self.draft[0].co) if self.draft else None
            closing = (start_px is not None and len(self.draft) >= 3 and
                       distance(start_px, self.mouse_px) <= CLOSE_PX * draw.ui_scale(context))
            if closing and not (tool == "INTERNAL" and
                                context.window_manager.cloth_next_pattern_ui.internal_kind == "LINE"):
                return self._finish_draft(context, tool)
            self.drag = {"kind": "poly_point", "co": co, "start_px": self.mouse_px, "handle": None}
            return True
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.drag is not None:
            self._finish_drag(context, region, rv3d, canvas, event)
            return True
        if event.value == "PRESS" and event.type in {"RET", "NUMPAD_ENTER"}:
            return self._finish_draft(context, tool)
        if event.value == "PRESS" and event.type == "BACK_SPACE":
            if self.draft:
                self.draft.pop()
                if self.draft:
                    last = self.draft[-1]
                    self.draft[-1] = replace(last, curve_out=bool(last.smooth))
                self.message = ""
            return True
        return False

    def _add_draft_point(self, context, drag):
        co, handle = drag["co"], drag.get("handle")
        if self.draft and distance(self.draft[-1].co, co) < 1e-4:
            return
        if handle is not None and math.hypot(*handle) > 1e-5:
            point = ContourPoint(0, co, (-handle[0], -handle[1]), handle, True, True)
            if self.draft:
                previous = self.draft[-1]
                self.draft[-1] = replace(previous, curve_out=True)
        else:
            point = ContourPoint(0, co)
        self.draft.append(point)
        self.message = ""

    def _draft_points(self, closing=True):
        points = list(self.draft)
        if closing and points and (points[0].handle_in != (0.0, 0.0)):
            points[-1] = replace(points[-1], curve_out=True)
        return tuple(points)

    def _finish_draft(self, context, tool):
        ui = context.window_manager.cloth_next_pattern_ui
        line = tool == "INTERNAL" and ui.internal_kind == "LINE"
        if len(self.draft) < (2 if line else 3):
            self.say("Add more points first" if self.draft else "", bool(self.draft))
            return True
        points = self._draft_points(closing=not line)
        if not line:
            ring = display_polyline(Contour(points))
            if signed_area(ring) < 0:
                points = _reverse(points)
        if tool == "POLYGON":
            ok = self._create(context, "Pattern", points, "POLYGON")
        elif tool == "CURVE_DRAW":
            ok = self._create(context, "Pattern", auto_smooth(points), "POLYGON")
        else:
            ok = self._create_internal(context, points, "LINE" if line else "HOLE")
        if ok:
            self.draft, self.draft_pattern = [], None
        return True

    def _create(self, context, name, canvas_points, kind, size=(0.0, 0.0)):
        scene = context.scene
        pattern = model.new_pattern(scene, name, canvas_points, kind, size)
        try:
            sync.rebuild(scene, pattern, force=True)
        except PatternError as exc:
            index = model.index_of(scene, pattern)
            scene.cloth_next_patterns.remove(index)
            self.say(f"Not created: {exc}", True)
            return False
        select_pattern(context, model.find(scene, scene.cloth_next_patterns[-1].identifier))
        self.say(f"Created {scene.cloth_next_patterns[-1].name}: "
                 f"{scene.cloth_next_patterns[-1].triangle_count} triangles")
        _undo("Create Pattern")
        return True

    def _create_internal(self, context, canvas_points, kind):
        scene = context.scene
        pattern = model.find(scene, self.draft_pattern)
        if pattern is None:
            self.say("The pattern was removed", True)
            return False
        location, rotation = model.placement(pattern)
        uids = model.allocate(pattern, len(canvas_points) + 1)
        local = tuple(ContourPoint(uid, to_local(p.co, location, rotation),
                                   vector_to_local(p.handle_in, rotation),
                                   vector_to_local(p.handle_out, rotation), p.curve_out, p.smooth)
                      for uid, p in zip(uids[1:], canvas_points))
        contours = [(c.kind, c.uid, model.read_points(c.points)) for c in pattern.contours]
        contours.append((kind, uids[0], local))
        ok, message = sync.commit(scene, pattern, contours=contours)
        if not ok:
            self.say(f"Not added: {message}", True)
            return False
        self.say("Hole added" if kind == "HOLE" else "Internal line added")
        _undo("Add Internal Shape")
        return True

    # -- Rectangle / Ellipse -------------------------------------------------------------------
    def _tool_rectangle(self, context, region, rv3d, canvas, event):
        return self._primitive_tool(context, region, rv3d, canvas, event, "rect")

    def _tool_ellipse(self, context, region, rv3d, canvas, event):
        return self._primitive_tool(context, region, rv3d, canvas, event, "ellipse")

    def _primitive_tool(self, context, region, rv3d, canvas, event, kind):
        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            anchor = self._snap(context, region, rv3d, canvas, event)
            self.drag = {"kind": kind, "anchor": anchor, "current": anchor,
                         "start_px": self.mouse_px}
            return True
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.drag is not None:
            self._finish_drag(context, region, rv3d, canvas, event)
            return True
        return False

    def _create_primitive(self, context, region, rv3d, drag):
        a, b = drag["anchor"], drag["current"]
        start_px = drag["start_px"]
        end_px = draw.canvas_to_region(region, rv3d, b)
        if end_px is None or distance(start_px, end_px) < 5 * draw.ui_scale(context):
            bpy.ops.clothnext.pattern_add_primitive(
                "INVOKE_DEFAULT", kind="RECTANGLE" if drag["kind"] == "rect" else "ELLIPSE",
                x=a[0], y=a[1])
            return
        width, height = abs(b[0] - a[0]), abs(b[1] - a[1])
        if width < 0.005 or height < 0.005:
            self.say("Too small: drag a larger shape", True)
            return
        center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if drag["kind"] == "rect":
            points = model.translate(rectangle_points(width, height, range(4)), center)
            self._create(context, "Rectangle", points, "RECTANGLE", (width, height))
        else:
            points = model.translate(ellipse_points(width / 2, height / 2, range(4)), center)
            self._create(context, "Ellipse", points, "ELLIPSE", (width, height))

    # -- Edit Pattern / Edit Curve ---------------------------------------------------------------
    def _tool_edit(self, context, region, rv3d, canvas, event):
        return self._edit_tool(context, region, rv3d, canvas, event, curve=False)

    def _tool_curve(self, context, region, rv3d, canvas, event):
        return self._edit_tool(context, region, rv3d, canvas, event, curve=True)

    def _edit_tool(self, context, region, rv3d, canvas, event, curve):
        scene = context.scene
        hover = self.hover
        if event.type == "LEFTMOUSE" and event.value != "RELEASE" and self.double and hover \
                and hover["kind"] == "segment" and not curve:
            pattern = model.find(scene, hover["pattern"])
            if pattern is not None:
                model.make_free_form(pattern)
                points = model.outline(pattern)
                uid = model.allocate(pattern)[0]
                new = editing.insert_point(points, hover["index"], hover["t"], uid)
                self._commit_points(context, pattern, new, "Add Point")
            self.drag = None
            return True
        if event.type == "LEFTMOUSE" and event.value in {"PRESS", "DOUBLE_CLICK"}:
            if hover is None:
                pattern = self._pattern_at(context, canvas)
                select_pattern(context, pattern, toggle=event.shift) if pattern else (
                    None if event.shift else select_pattern(context, None))
                self.points_selected = {}
                self.selected_segment = None
                self._selection_changed(context)
                return True
            pattern = model.find(scene, hover["pattern"])
            if pattern is None:
                return True
            if not draw.is_selected(pattern):
                select_pattern(context, pattern, extend=event.shift)
            points = model.outline(pattern)
            base = {"pattern": pattern.identifier, "orig": points, "start": canvas,
                    "moved": False, "valid": True, "live": False}
            if hover["kind"] == "handle":
                self.drag = dict(base, kind="handle", index=hover["index"], side=hover["side"],
                                 symmetric=not event.alt and None)
            elif hover["kind"] == "point":
                if event.alt and curve:
                    p = points[hover["index"]]
                    new = list(points)
                    new[hover["index"]] = replace(p, smooth=not p.smooth)
                    self._commit_points(context, pattern, tuple(new), "Toggle Smooth")
                    return True
                selected = self.points_selected.setdefault(pattern.identifier, set())
                if event.shift:
                    selected ^= {hover["index"]}
                elif hover["index"] not in selected:
                    selected.clear()
                    selected.add(hover["index"])
                for other in list(self.points_selected):
                    if other != pattern.identifier and not event.shift:
                        del self.points_selected[other]
                self.selected_segment = None
                self.drag = dict(base, kind="points", indices=sorted(selected) or [hover["index"]],
                                 primary=hover["index"])
            elif hover["kind"] == "segment":
                n = len(points)
                self.points_selected = {}
                self.selected_segment = (pattern.identifier, hover["index"] % n)
                if curve:
                    self.drag = dict(base, kind="bend", index=hover["index"], t=hover["t"])
                else:
                    self.drag = dict(base, kind="segment",
                                     indices=[hover["index"] % n, (hover["index"] + 1) % n],
                                     primary=hover["index"] % n)
            elif hover["kind"] == "pattern":
                select_pattern(context, pattern, toggle=event.shift)
                self.points_selected, self.selected_segment = {}, None
                self._start_move(context, canvas)
            self._selection_changed(context)
            return True
        if event.type == "LEFTMOUSE" and event.value == "RELEASE" and self.drag is not None:
            self._finish_drag(context, region, rv3d, canvas, event)
            self._selection_changed(context)
            return True
        if event.value == "PRESS" and event.type in {"X", "DEL"} and self.drag is None:
            removed = False
            for identifier, indices in list(self.points_selected.items()):
                pattern = model.find(scene, identifier)
                if pattern is None or not indices:
                    continue
                points = model.outline(pattern)
                if len(points) - len(indices) < 3:
                    self.say("A pattern needs at least 3 points", True)
                    return True
                model.make_free_form(pattern)
                new = tuple(p for i, p in enumerate(points) if i not in indices)
                removed = self._commit_points(context, pattern, new, "Remove Point") or removed
            self.points_selected = {}
            return removed or bool(selected_patterns(scene)) and self._common_keys(
                context, region, rv3d, canvas, event)
        return False

    def _edit_drag(self, context, region, rv3d, canvas, event):
        drag = self.drag
        scene = context.scene
        pattern = model.find(scene, drag["pattern"])
        if pattern is None:
            self.drag = None
            return
        orig = drag["orig"]
        location, rotation = model.placement(pattern)
        drag["moved"] = True
        kind = drag["kind"]
        if kind in {"points", "segment"}:
            primary = drag["primary"]
            origin_canvas = to_canvas(orig[primary].co, location, rotation)
            exclude = {(pattern.identifier, i) for i in drag["indices"]}
            target = self._snap(context, region, rv3d, canvas, event, exclude=exclude,
                                origin=to_canvas(orig[primary - 1].co, location, rotation)
                                if event.shift else None)
            if kind == "segment" and not event.shift:
                target = add(origin_canvas, sub(canvas, drag["start"]))
            delta = vector_to_local(sub(target, origin_canvas), rotation)
            new = editing.move_points(orig, drag["indices"], delta)
        elif kind == "bend":
            new = editing.bend_segment(orig, drag["index"], drag["t"],
                                       self._local(pattern, canvas))
        else:
            local = self._local(pattern, canvas)
            point = orig[drag["index"]]
            offset = sub(local, point.co)
            new = editing.set_handle(orig, drag["index"], drag["side"], offset,
                                     symmetric=None if drag["symmetric"] is None else False)
        try:
            validate_ring(display_polyline(Contour(new), 0.001))
            drag["valid"] = True
            self.message = ""
        except PatternError as exc:
            drag["valid"] = False
            self.say(str(exc), True)
        drag["candidate"] = new
        self.preview = (pattern.identifier, new, drag["valid"])
        now = time.perf_counter()
        if drag["valid"] and pattern.mesh_time < LIVE_MESH_BUDGET and now - self.last_live > 0.03:
            model.make_free_form(pattern)
            ok, _message = sync.commit(scene, pattern, new, seams=False)
            drag["live"] = drag["live"] or ok
            self.last_live = now

    def _edit_release(self, context, drag):
        scene = context.scene
        self.preview = None
        pattern = model.find(scene, drag["pattern"])
        if pattern is None or not drag["moved"] or "candidate" not in drag:
            return
        if not drag["valid"]:
            if drag["live"]:
                sync.commit(scene, pattern, drag["orig"])
            self.say(f"Edit rejected: {self.message or 'invalid outline'}", True)
            return
        label = {"points": "Move Point", "segment": "Move Edge", "bend": "Edit Curve",
                 "handle": "Edit Curve Handle"}[drag["kind"]]
        model.make_free_form(pattern)
        if not self._commit_points(context, pattern, drag["candidate"], label, undo=True):
            if drag["live"]:
                sync.commit(scene, pattern, drag["orig"])

    def _commit_points(self, context, pattern, points, label, undo=True):
        scene = context.scene
        ok, message = sync.commit(scene, pattern, points)
        if not ok:
            self.say(f"Edit rejected: {message}", True)
            return False
        from . import operators
        operators.sync_symmetry(scene, pattern)
        self.say(f"{label}: {pattern.triangle_count} triangles, "
                 f"{pattern.mesh_time * 1000:.0f} ms")
        if undo:
            _undo(label)
        return True

    # -- Sewing ------------------------------------------------------------------------------
    def _tool_sewing(self, context, region, rv3d, canvas, event):
        scene = context.scene
        if event.value == "PRESS" and event.type in {"X", "DEL"} and self.hover_seam:
            bpy.ops.clothnext.pattern_seam_remove(identifier=self.hover_seam)
            self.hover_seam = None
            return True
        if event.value == "PRESS" and event.type == "F" and self.hover_seam:
            for seam in scene.cloth_next_pattern_seams:
                if seam.identifier == self.hover_seam:
                    seam.flipped = not seam.flipped
                    _undo("Flip Seam")
            return True
        if event.type != "LEFTMOUSE" or event.value not in {"PRESS", "DOUBLE_CLICK"}:
            return False
        if self.hover_seam and self.seam_first is None and not event.shift:
            for index, seam in enumerate(scene.cloth_next_pattern_seams):
                if seam.identifier == self.hover_seam:
                    scene.cloth_next_pattern_settings.seam_index = index
                    self.say(f"Seam {seam.name}  ·  F reverses  ·  X deletes  ·  "
                             "Shift-click an edge to start a new seam")
            self._selection_changed(context)
            return True
        hover = self.hover
        if not hover or hover["kind"] != "segment":
            self.say("Click a pattern edge", True)
            return True
        pattern = model.find(scene, hover["pattern"])
        points = model.outline(pattern)
        n = len(points)
        side = (pattern.identifier, points[hover["index"] % n].uid,
                points[(hover["index"] + 1) % n].uid, hover["t"], hover["index"])
        if self.seam_first is None:
            self.seam_first = side
            self.say("Now click the edge to sew it to")
            return True
        first, self.seam_first = self.seam_first, None
        if first[:3] == side[:3]:
            self.say("Choose a different edge", True)
            return True
        bpy.ops.clothnext.pattern_seam_add(
            a_pattern=first[0], a_start=first[1], a_end=first[2],
            b_pattern=side[0], b_start=side[1], b_end=side[2],
            flipped=(first[3] < 0.5) != (side[3] < 0.5))
        self.say("Seam created  ·  F flips it  ·  X deletes it")
        return True

    # -- drawing --------------------------------------------------------------------------------
    HINTS = {
        "SELECT": "Click a pattern · drag to move · R rotate · Shift D duplicate · X delete",
        "POLYGON": "Click points · drag for a curve point · click the first point or Enter "
                   "closes · Backspace undoes a point · Shift 45° · Ctrl no snap",
        "CURVE_DRAW": "Click points of a smooth outline · click the first point or Enter "
                      "closes · Backspace undoes a point",
        "RECTANGLE": "Drag a rectangle · Shift square · click for exact size",
        "ELLIPSE": "Drag an ellipse · Shift circle · click for exact size",
        "EDIT": "Drag points or edges · double-click an edge adds a point · X removes points",
        "CURVE": "Drag an edge to bend it · drag handles · Alt-click a point: smooth/corner",
        "INTERNAL": "Click points inside a pattern · L hole/line · Enter finishes",
        "SEWING": "Click an edge, then the matching edge (near the ends that belong together)"
                  " · click a seam to select it",
    }

    def draw_view(self, context, region, rv3d):
        scene = context.scene
        tool = context.window_manager.cloth_next_pattern_ui.tool
        pal = style.palette(context)
        accent = (*style.ACCENT, 1.0)
        if tool == "CURVE":
            pairs = []
            for pattern in draw.visible_patterns(scene):
                if not (draw.is_selected(pattern) or self.hover_pattern() == pattern.identifier):
                    continue
                points = model.outline(pattern)
                n = len(points)
                for i, p in enumerate(points):
                    co = sync.canvas_of(pattern, p.co)
                    if p.curve_out:
                        pairs += [co, sync.canvas_of(pattern, add(p.co, p.handle_out))]
                    if points[(i - 1) % n].curve_out:
                        pairs += [co, sync.canvas_of(pattern, add(p.co, p.handle_in))]
            draw.lines(pairs, (*pal.text[:3], 0.55), 1.0, region=region)
        hover = self.hover
        selected_segment = self.selected_segment
        if selected_segment is not None and tool in {"EDIT", "CURVE"}:
            pattern = model.find(scene, selected_segment[0])
            if pattern is not None:
                line = editing.segment_points(model.outline(pattern), selected_segment[1])
                draw.polyline(model.to_canvas_points(pattern, line), accent, 3.0, region=region)
        if hover and hover["kind"] == "segment":
            pattern = model.find(scene, hover["pattern"])
            if pattern is not None:
                line = editing.segment_points(model.outline(pattern), hover["index"])
                color = (*style.ACCENT, 0.85) if tool == "SEWING" else (*pal.text[:3], 0.9)
                draw.polyline(model.to_canvas_points(pattern, line), color, 3.0, region=region)
        if self.seam_first is not None:
            self._draw_seam_preview(context, region, rv3d)
        if self.preview is not None:
            identifier, points, valid = self.preview
            pattern = model.find(scene, identifier)
            if pattern is not None:
                ring = model.to_canvas_points(pattern, display_polyline(Contour(points)))
                if valid:
                    try:
                        draw.triangles(ring, draw.ear_fill(ring), (*style.ACCENT, 0.10))
                    except Exception:
                        pass
                draw.polyline(ring, accent if valid else (*style.ERROR, 1.0), 1.6, closed=True,
                              region=region)
        drag = self.drag
        if drag is not None and drag["kind"] == "box":
            a, b = drag["start"], drag.get("end", drag["start"])
            draw.polyline([a, (b[0], a[1]), b, (a[0], b[1])], (*pal.text[:3], 0.7), 1.0,
                          True, region)
        if drag is not None and drag["kind"] in {"rect", "ellipse"}:
            a, b = drag["anchor"], drag["current"]
            w, h = abs(b[0] - a[0]), abs(b[1] - a[1])
            if w > 0 and h > 0:
                center = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                pts = (rectangle_points(w, h, range(4)) if drag["kind"] == "rect"
                       else ellipse_points(w / 2, h / 2, range(4)))
                ring = [add(p, center) for p in display_polyline(Contour(pts))]
                draw.triangles(ring, draw.ear_fill(ring), (*pal.fill[:3], 0.55))
                draw.polyline(ring, accent, 1.6, True, region)
        if self.draft:
            self._draw_draft(context, region, rv3d, tool, pal)
        if drag is not None and drag["kind"] == "rotate":
            draw.dashed([drag["center"], self.cursor or drag["center"]], (*pal.text[:3], 0.6),
                        1.0, 6 * self._px(region, rv3d), region)

    def _draw_seam_preview(self, context, region, rv3d):
        """Exactly which stretches will be sewn, and how they pair."""
        scene = context.scene
        identifier, start, end, t_first, _index = self.seam_first
        pattern = model.find(scene, identifier)
        if pattern is None:
            return
        first = model.to_canvas_points(pattern, side_polyline(
            model.outline(pattern), SeamSide(identifier, start, end)))
        accent = (*style.ACCENT, 1.0)
        draw.polyline(first, accent, 3.0, region=region)
        hover = self.hover
        if not hover or hover["kind"] != "segment":
            return
        other = model.find(scene, hover["pattern"])
        if other is None:
            return
        second = model.to_canvas_points(other, editing.segment_points(
            model.outline(other), hover["index"]))
        crossed = (t_first < 0.5) != (hover["t"] < 0.5)
        a = draw._resample_line(first, 5)
        b = draw._resample_line(second, 5)
        if crossed:
            b = list(reversed(b))
        px = self._px(region, rv3d)
        for p, q in zip(a, b):
            draw.dashed([p, q], (*style.ACCENT, 0.7), 1.0, 5 * px, region)

    def _draft_ring(self, tool):
        cursor = self.snapped.co if self.snapped else self.cursor
        points = list(self.draft)
        if cursor is not None and self.drag is None:
            points.append(ContourPoint(0, cursor))
        elif self.drag is not None and self.drag.get("kind") == "poly_point":
            handle = self.drag.get("handle")
            co = self.drag["co"]
            if handle:
                if points:
                    points[-1] = replace(points[-1], curve_out=True)
                points.append(ContourPoint(0, co, (-handle[0], -handle[1]), handle, True, True))
            else:
                points.append(ContourPoint(0, co))
        return points

    def _draw_draft(self, context, region, rv3d, tool, pal):
        line_mode = tool == "INTERNAL" and \
            context.window_manager.cloth_next_pattern_ui.internal_kind == "LINE"
        points = self._draft_ring(tool)
        if tool == "CURVE_DRAW" and len(points) >= 3:
            points = list(auto_smooth(tuple(points)))
        if len(points) >= 3 and not line_mode:
            ring = display_polyline(Contour(tuple(points)))
            try:
                validate_ring(ring)
                draw.triangles(ring, draw.ear_fill(ring), (*pal.fill[:3], 0.45))
                closing = (*pal.text[:3], 0.35)
            except PatternError:
                closing = (*style.ERROR, 0.9)
            draw.dashed([ring[-1], ring[0]], closing, 1.0, 5 * self._px(region, rv3d), region)
        # Open contour: the same segments as the closed preview, minus the closing one.
        draw.polyline(display_polyline(Contour(tuple(points), False)), (*style.ACCENT, 1.0),
                      1.8, region=region)

    def draw_pixel(self, context, region, rv3d):
        tool = context.window_manager.cloth_next_pattern_ui.tool
        s = style.ui_scale(context)
        pal = style.palette(context)
        scene = context.scene
        accent = (*style.ACCENT, 1.0)
        vertex = style.VERTEX * s
        if tool in {"EDIT", "CURVE", "INTERNAL"}:
            for pattern in draw.visible_patterns(scene):
                if not (draw.is_selected(pattern) or self.hover_pattern() == pattern.identifier):
                    continue
                points = model.outline(pattern)
                chosen = self.points_selected.get(pattern.identifier, set())
                n = len(points)
                for i, p in enumerate(points):
                    xy = draw.canvas_to_region(region, rv3d, sync.canvas_of(pattern, p.co))
                    if xy is None:
                        continue
                    if i in chosen:
                        style.square(xy[0], xy[1], vertex + 1 * s, accent, pal.canvas)
                    elif p.smooth:
                        style.disc(xy[0], xy[1], vertex / 2 + 1 * s, pal.canvas)
                        style.disc(xy[0], xy[1], vertex / 2, pal.text)
                    else:
                        style.square(xy[0], xy[1], vertex, pal.text, pal.canvas)
                    if tool == "CURVE":
                        for offset, on in ((p.handle_out, p.curve_out),
                                           (p.handle_in, points[(i - 1) % n].curve_out)):
                            if on:
                                h = draw.canvas_to_region(region, rv3d, sync.canvas_of(
                                    pattern, add(p.co, offset)))
                                if h is not None:
                                    style.diamond(h[0], h[1], vertex + 1 * s, pal.text)
        hover = self.hover
        if hover and hover["kind"] in {"point", "handle"}:
            pattern = model.find(scene, hover["pattern"])
            if pattern is not None:
                p = model.outline(pattern)[hover["index"]]
                co = p.co if hover["kind"] == "point" else add(
                    p.co, p.handle_out if hover["side"] == "out" else p.handle_in)
                xy = draw.canvas_to_region(region, rv3d, sync.canvas_of(pattern, co))
                if xy:
                    style.ring(xy[0], xy[1], vertex + 2 * s, accent, 1.5 * s, region=region)
        if self.selected_segment is not None and tool in {"EDIT", "CURVE"} and \
                scene.cloth_next_pattern_settings.show_lengths:
            pattern = model.find(scene, self.selected_segment[0])
            if pattern is not None and not draw.is_selected(pattern):
                draw._segment_labels(context, region, rv3d, pal, pattern,
                                     only=self.selected_segment[1])
        if self.draft:
            pts = [draw.canvas_to_region(region, rv3d, p.co) for p in self.draft]
            for xy in pts:
                if xy:
                    style.square(xy[0], xy[1], vertex, pal.text, pal.canvas)
            if pts and pts[0] and len(self.draft) >= 3 and self.mouse_px and \
                    distance(pts[0], self.mouse_px) <= CLOSE_PX * s:
                style.ring(pts[0][0], pts[0][1], 7 * s, accent, 2.0 * s, region=region)
                style.label("Close", pts[0][0], pts[0][1] + 12 * s, pal, accent, 10, scale=s)
            if scene.cloth_next_pattern_settings.show_lengths:
                ring = self._draft_ring(tool)
                for number, (a, b) in enumerate(zip(ring, ring[1:])):
                    pa = draw.canvas_to_region(region, rv3d, a.co)
                    pb = draw.canvas_to_region(region, rv3d, b.co)
                    value = draw.format_length(context, distance(a.co, b.co))
                    if not pa or not pb:
                        continue
                    active = number == len(ring) - 2
                    if not active and distance(pa, pb) < style.text_width(value, 10, s) + 16 * s:
                        continue
                    style.label(value, (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2 + 8 * s, pal,
                                pal.text if active else pal.text_dim, 10, scale=s)
        snapped = self.snapped
        show_snap = snapped is not None and snapped.kind != "NONE" and (
            self.drag is None or self.drag["kind"] in {"points", "rect", "ellipse", "poly_point"})
        if show_snap:
            xy = draw.canvas_to_region(region, rv3d, snapped.co)
            if xy:
                r = 5 * s
                style.lines_px([(xy[0] - r, xy[1]), (xy[0] + r, xy[1]),
                                (xy[0], xy[1] - r), (xy[0], xy[1] + r)], accent, 1.2 * s, region)
                if snapped.kind in {"VERTEX", "EDGE"}:
                    style.ring(xy[0], xy[1], r + 2 * s, accent, 1.2 * s, region=region)
                style.text(snapped.kind.title(), xy[0] + 9 * s, xy[1] - 15 * s,
                           (*pal.text_dim[:3], 0.9), 9, scale=s)
        drag = self.drag
        if drag is not None and drag["kind"] in {"rect", "ellipse"}:
            a, b = drag["anchor"], drag["current"]
            xy = draw.canvas_to_region(region, rv3d, b)
            if xy:
                style.label(f"{draw.format_length(context, abs(b[0] - a[0]))} × "
                            f"{draw.format_length(context, abs(b[1] - a[1]))}",
                            xy[0] + 12 * s, xy[1] + 10 * s, pal, pal.text, 10, "LEFT", s)
        dock.draw(context, region)
        executing = bool(self.draft) or (drag is not None and drag["kind"] not in {"box"})
        toolbar.draw(context, region, self.toolbar_hover, executing=executing,
                     error=self.message_error and bool(self.message),
                     has_selection=bool(selected_patterns(scene)))
        step = draw.grid_step(context, region, rv3d)
        toolbar.draw_status(context, region, tool, self.HINTS.get(tool, ""), self.message,
                            self.message_error,
                            f"Grid {draw.format_length(context, step)}  ·  Front Ortho")


class _NoShift:
    """Event proxy without Shift, so square/circle drags still snap the corner."""

    def __init__(self, event):
        self._event = event
        self.shift = False

    def __getattr__(self, name):
        return getattr(self._event, name)


_DOCK = object()  # the docked inspector column (a chrome region of the 2D editor)


def auto_smooth(points):
    """Curve tool: every point becomes smooth with Catmull-Rom tangents.

    A point that already has handles (dragged while drawing) keeps them.
    """
    n = len(points)
    result = []
    for i, p in enumerate(points):
        if p.handle_out != (0.0, 0.0) or p.handle_in != (0.0, 0.0):
            result.append(replace(p, curve_out=True, smooth=True))
            continue
        prev, nxt = points[(i - 1) % n].co, points[(i + 1) % n].co
        tangent = ((nxt[0] - prev[0]) / 6.0, (nxt[1] - prev[1]) / 6.0)
        result.append(replace(p, handle_out=tangent, handle_in=(-tangent[0], -tangent[1]),
                              curve_out=True, smooth=True))
    return tuple(result)


def _reverse(points):
    """Reverse a closed outline's direction keeping every segment's curve."""
    n = len(points)
    result = []
    for j in range(n - 1, -1, -1):
        p, previous = points[j], points[(j - 1) % n]
        result.append(replace(p, handle_out=p.handle_in, handle_in=p.handle_out,
                              curve_out=previous.curve_out))
    return tuple(result)


# -- automatic start --------------------------------------------------------------------------

def _autostart():
    try:
        wm = bpy.context.window_manager
        for window in wm.windows:
            areas = workspace.window_pattern_areas(window, repair=True)
            if not areas:
                continue
            workspace.configure_screen(window.screen)
            workspace.close_side_areas(window)
            for area in areas:
                workspace.refresh_regions(area)
            if session_for(window) is not None:
                continue
            if getattr(window, "modal_operators", ()):
                continue
            obj = window.view_layer.objects.active
            if obj is not None and obj.mode != "OBJECT":
                continue
            area = areas[0]
            region = workspace.window_region(area)
            with bpy.context.temp_override(window=window, screen=window.screen, area=area,
                                           region=region):
                bpy.ops.clothnext.pattern_editor("INVOKE_DEFAULT")
    except Exception:
        _log.exception("PATTERN NeXt editor could not start")
    return 0.25


def reset_transient():
    """After undo/redo: drop drags and previews that refer to old data."""
    for op in tuple(_sessions.values()):
        if op.drag is not None:
            op.drag = None
        op.preview = None
        op.hover = None


def register():
    if not bpy.app.timers.is_registered(_autostart):
        bpy.app.timers.register(_autostart, first_interval=0.5, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_autostart):
        bpy.app.timers.unregister(_autostart)
    for op in tuple(_sessions.values()):
        op.finish()


CLASSES = (CLOTHNEXT_OT_pattern_editor,)
