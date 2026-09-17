# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional viewport presentation for the existing Simulation workflow.

Blender gizmos own the clickable areas; the draw handler only paints the bar.
Quick Assign owns a short-lived gesture; simulation state remains elsewhere.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import bpy

from ..bake.controller import shared_controller
from ..pull_detach import PullGesture, logo_hit
from ..solver_quality import matching_quality_preset
from . import object_properties, physics_operators, physics_ui, quick_assign
from .addon_identity import addon_preferences

_handle = None
_registered = False
_images = {}
_pull_sessions = {}
_toolbar_visible = True
_slide = 1.0
_animation_from = 1.0
_animation_start_time = None
_ANIMATION_DURATION = 0.135
_last_enabled = False
_keymap = None
_keymap_item = None
_LAYOUT_SCALE = 0.65
_persistent = getattr(getattr(bpy.app, "handlers", None),
                      "persistent", lambda fn: fn)
_BG = (0.105, 0.119, 0.130, 0.94)  # charcoal
_SURFACE = (0.225, 0.245, 0.265, 0.97)
_CYAN = (0.06, 0.66, 0.85, 1.0)
_BLUE = (0.0, 0.60, 0.85, 1.0)
_TEXT = (0.93, 0.96, 0.98, 1.0)
_MUTED = (0.57, 0.64, 0.68, 1.0)
_DIR_READY = (0.37, 0.68, 0.17, 1.0)
_DIR_MISSING = (0.78, 0.19, 0.22, 1.0)


def _prefs(context):
    try:
        return addon_preferences(context, __package__)
    except (KeyError, AttributeError):
        return None


def enabled(context):
    prefs = _prefs(context)
    return bool(prefs and getattr(prefs, "new_look", False))


def visible(context):
    return enabled(context) and _slide_fraction() > 0.0


def _slide_fraction(now=None):
    """Current visible fraction; reversing F6 starts at this exact position."""
    global _slide, _animation_start_time
    if _animation_start_time is None:
        return _slide
    elapsed = max(0.0, (time.monotonic() if now is None else now)
                  - _animation_start_time)
    target = float(_toolbar_visible)
    duration = _ANIMATION_DURATION * abs(target - _animation_from)
    t = min(1.0, elapsed / duration) if duration else 1.0
    eased = 1.0 - (1.0 - t)**2 if target else t**2
    _slide = _animation_from + (target - _animation_from) * eased
    if t >= 1.0:
        _slide = target
        _animation_start_time = None
    return _slide


def _stop_animation():
    global _animation_start_time
    _animation_start_time = None
    if bpy.app.timers.is_registered(_animation_tick):
        bpy.app.timers.unregister(_animation_tick)


def _animation_tick():
    _slide_fraction()
    _tag_redraw(bpy.context)
    return 1.0 / 60.0 if _animation_start_time is not None else None


def _scale(context):
    return max(0.75, float(context.preferences.system.ui_scale)) * _LAYOUT_SCALE


def _quality_width(context):
    scene = getattr(context, "scene", None)
    if scene is None:
        label = "Custom"
    else:
        preset = matching_quality_preset(
            object_properties.solver_quality_from(scene),
            has_pdrd=physics_operators.scene_has_pdrd(scene))
        label = preset.label if preset else "Custom"
    return max(66, 25 + 8 * len(label))


def _bounds(context):
    region = context.region
    if region is None or region.type != "WINDOW":
        return None
    scale = _scale(context)
    width, height = (230 + _quality_width(context)) * scale, 54 * scale
    if (region.width < width + 24 * scale
            or region.height < height + 78 * (scale / _LAYOUT_SCALE)):
        return None
    return ((region.width - width) / 2, 28 * scale, width, height, scale)


def _animated_bounds(context):
    bounds = _bounds(context)
    if bounds is None:
        return None
    x, shown_y, width, height, scale = bounds
    # The WINDOW region clips the toolbar as it passes below its own edge.
    # Include the detached Quick Assign button in the fully clipped extent.
    hidden_y = -height - 50 * (scale / _LAYOUT_SCALE)
    y = hidden_y + (shown_y - hidden_y) * _slide_fraction()
    return x, y, width, height, scale


def _state(context):
    scene = getattr(context, "scene", None)
    snapshot = shared_controller.snapshot()
    if scene is None:
        return None, snapshot, None, False, "No scene"
    quality = matching_quality_preset(
        object_properties.solver_quality_from(scene),
        has_pdrd=physics_operators.scene_has_pdrd(scene))
    model = physics_ui._bake_panel_model(
        context, physics_ui._active_backend_status(context))
    deformables = [obj for obj in scene.objects
                   if getattr(getattr(obj, "cloth_next", None), "enabled", False)
                   and obj.cloth_next.role in {"CLOTH", "ROD", "SOFT_BODY", "RIGID_BODY"}]
    # The same existing property and missing-directory rule used by Bake.
    directory_ok = bool(deformables) and all(
        path and os.path.isdir(bpy.path.abspath(path)) for path in
        (str(getattr(obj.cloth_next, "cache_directory", "") or "").strip()
         for obj in deformables))
    recovery = getattr(scene, "cloth_next_recovery", None)
    recovery_status = (str(getattr(recovery, "status", "") or "")
                       if getattr(recovery, "enabled", False) else "")
    message = snapshot.error_summary
    if not message and recovery_status in {
            "Recovery Check Failed", "Recovery Metadata Invalid",
            "Recovery Incompatible", "Recovery Project Missing"}:
        message = recovery_status
    message = message or (physics_ui._run_state_text(snapshot) if snapshot.active
                          else model.reason)
    return model, snapshot, quality, directory_ok, message


def _rounded(shader, batch_for_shader, x, y, w, h, r, color):
    import math
    points = []
    for cx, cy, start in ((x+w-r, y+r, -90), (x+w-r, y+h-r, 0),
                          (x+r, y+h-r, 90), (x+r, y+r, 180)):
        for i in range(7):
            a = math.radians(start + i * 15)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    verts = [(x+w/2, y+h/2)] + points
    indices = [(0, i+1, (i+1) % len(points)+1) for i in range(len(points))]
    shader.bind()
    shader.uniform_float("color", color)
    batch_for_shader(shader, "TRIS", {"pos": verts}, indices=indices).draw(shader)


def _label(blf, text, x, y, size, color):
    import gpu
    blf.size(0, size)
    gpu.state.blend_set("ALPHA")
    blf.color(0, *color)
    blf.position(0, x, y, 0)
    blf.draw(0, text)


def _centered_label(blf, text, x, y, width, size, color):
    blf.size(0, size)
    text_width, _ = blf.dimensions(0, text)
    _label(blf, text, x + (width - text_width) / 2, y, size, color)


def _centered_glyph(blf, text, cx, cy, size, color):
    blf.size(0, size)
    width, height = blf.dimensions(0, text)
    _label(blf, text, cx - width / 2, cy - height / 2, size, color)


def _asset_icon(gpu, batch_for_shader, name, x, y, size):
    image = _images.get(name)
    try:
        if image is not None and image.name not in bpy.data.images:
            image = None
    except ReferenceError:
        image = None
    if image is None:
        path = Path(__file__).resolve().parent.parent / "assets" / "icons" / f"{name}.png"
        if not path.is_file():
            return
        image = bpy.data.images.load(str(path), check_existing=False)
        _images[name] = image
    texture = gpu.texture.from_image(image)
    shader = gpu.shader.from_builtin("IMAGE")
    batch = batch_for_shader(shader, "TRI_FAN", {
        "pos": ((x, y), (x+size, y), (x+size, y+size), (x, y+size)),
        "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1)),
    })
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)


@_persistent
def _scene_loading(_dummy):
    quick_assign.cancel_all()
    _cancel_pulls()


@_persistent
def _scene_loaded(_dummy):
    # Loading a .blend frees images from the old Main, invalidating RNA refs.
    _images.clear()
    if _registered:
        _tag_redraw(bpy.context)


def _message_anchor(bounds):
    """Detached diagnostics, aligned beside Bake and outside the pill."""
    x, y, width, height, scale = bounds
    return x + width + 18*(scale/_LAYOUT_SCALE), y + height/2


def _fit_label(blf, text, available, size):
    blf.size(0, size)
    if blf.dimensions(0, text)[0] <= available:
        return text
    if blf.dimensions(0, "…")[0] > available:
        return ""
    while text and blf.dimensions(0, text + "…")[0] > available:
        text = text[:-1]
    return text + "…"


def _draw():
    context = bpy.context
    if not visible(context):
        return
    bounds = _animated_bounds(context)
    if bounds is None:
        return
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader

    x, y, w, h, s = bounds
    quality_width = _quality_width(context)
    bake_x = 136 + quality_width
    model, snapshot, quality, directory_ok, message = _state(context)
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    try:
        _draw_pull(context, bounds, blf, shader, batch_for_shader)
        _rounded(shader, batch_for_shader, x, y, w, h, 25*s, _BG)
        shader.bind()
        shader.uniform_float("color", (0.56, 0.63, 0.68, 0.38))
        batch_for_shader(shader, "LINES", {"pos": ((x+57*s, y+12*s),
                                                 (x+57*s, y+42*s))}).draw(shader)
        _rounded(shader, batch_for_shader, x+68*s, y+8*s, 46*s, h-16*s,
                 17*s, _SURFACE)
        _rounded(shader, batch_for_shader, x+126*s, y+8*s,
                 quality_width*s, h-16*s,
                 17*s, _DIR_MISSING if quality and
                 quality.identifier == "EXTREME" else _SURFACE)
        bake_ready = bool((snapshot.active and snapshot.can_cancel) or
                          (not snapshot.active and model and model.enabled))
        _rounded(shader, batch_for_shader, x+bake_x*s, y+8*s, 82*s, h-16*s,
                 17*s, _BLUE if bake_ready else (0.12, 0.13, 0.14, 0.88))
        _rounded(shader, batch_for_shader, x+105*s, y+37*s, 18*s, 18*s,
                 9*s, _DIR_READY if directory_ok else _DIR_MISSING)
        _asset_icon(gpu, batch_for_shader, "cloth_next", x+14*s, y+10*s, 34*s)
        _asset_icon(gpu, batch_for_shader, "folder", x+81*s, y+17*s, 20*s)
        _centered_glyph(blf, "✓" if directory_ok else "×",
                        x+114*s, y+46*s, round(12*s), _TEXT)
        _centered_label(blf, (quality.label if quality else "Custom") + " ▾",
                        x+126*s, y+23*s, quality_width*s, round(13*s), _TEXT)
        _centered_label(
            blf, "BAKE" if not snapshot.active else "CANCEL",
            x+bake_x*s, y+23*s-1.5*(s/_LAYOUT_SCALE),
            82*s, round(14*s),
            _TEXT if (snapshot.active and snapshot.can_cancel) or
            (model and model.enabled) else _MUTED)
        if message:
            recovery = getattr(getattr(context, "scene", None),
                               "cloth_next_recovery", None)
            error = bool(snapshot.error_summary or (model and model.reason))
            error |= str(getattr(recovery, "status", "") or "") in {
                "Recovery Check Failed", "Recovery Metadata Invalid",
                "Recovery Incompatible", "Recovery Project Missing"}
            ui = s / _LAYOUT_SCALE
            ix, iy = _message_anchor(bounds)
            color = _DIR_MISSING if error else (1.0, 1.0, 1.0, 1.0)
            gpu.state.blend_set("ALPHA")
            _rounded(shader, batch_for_shader,
                     ix-7*ui, iy-7*ui, 14*ui, 14*ui, 7*ui,
                     _DIR_MISSING if error else _BLUE)
            _centered_glyph(blf, "!" if error else "i", ix, iy,
                            round(10*ui), (1.0, 1.0, 1.0, 1.0))
            available = max(0, context.region.width - (ix+25*ui))
            label = _fit_label(blf, str(message), available, round(11*ui))
            _label(blf, label,
                   ix+15*ui, iy-4*ui, round(11*ui), color)
        quick_assign.draw(context, blf, gpu, batch_for_shader, shader)
    finally:
        gpu.state.blend_set("NONE")


def _cancel_pulls():
    for operator in tuple(_pull_sessions.values()):
        operator.finish()


class CLOTHNEXT_OT_pull_detach(quick_assign.CLOTHNEXT_OT_quick_assign):
    bl_idname = "clothnext.pull_detach"
    bl_label = "Remove Cloth NeXt Physics"
    bl_description = "Hold and pull left to remove Cloth NeXt Physics. Baked playback is preserved."
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return (getattr(context.area, "type", None) == "VIEW_3D"
                and getattr(context.region, "type", None) == "WINDOW"
                and visible(context) and not shared_controller.snapshot().active)

    def invoke(self, context, event):
        bounds = _animated_bounds(context)
        if (event.type != "LEFTMOUSE" or event.value != "PRESS"
                or not self.poll(context) or _animation_start_time is not None
                or not logo_hit(bounds, event.mouse_region_x, event.mouse_region_y)):
            return {"CANCELLED"}
        targets = tuple(obj for obj in context.selected_objects
                        if getattr(getattr(obj, "cloth_next", None), "enabled", False))
        if not physics_operators.removal_targets_valid(targets):
            return {"CANCELLED"}
        self.key = quick_assign.region_key(context)
        if self.key in _pull_sessions:
            return {"CANCELLED"}
        self.window, self.area, self.region = context.window, context.area, context.region
        self.screen, self.workspace = self.window.screen, self.window.workspace
        self.scene, self.view_layer = context.scene, context.view_layer
        self.wm = context.window_manager
        self.bounds = bounds
        self.targets = targets
        self.gesture = PullGesture(event.mouse_region_x, 160*bounds[4])
        self.timer = None
        _pull_sessions[self.key] = self
        try:
            self.wm.modal_handler_add(self)
        except Exception:
            self.finish()
            raise
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def valid_context(self, context):
        try:
            return (_registered and visible(context) and _animation_start_time is None
                    and context.window == self.window and self.window in tuple(self.wm.windows)
                    and self.window.screen == self.screen and self.window.workspace == self.workspace
                    and self.area in tuple(self.screen.areas) and self.region in tuple(self.area.regions)
                    and context.area == self.area and context.region == self.region
                    and context.scene == self.scene and context.view_layer == self.view_layer
                    and _animated_bounds(context) == self.bounds
                    and all(any(obj is candidate for candidate in self.scene.objects)
                            for obj in self.targets)
                    and physics_operators.removal_targets_valid(self.targets))
        except (ReferenceError, AttributeError):
            return False

    def modal(self, context, event):
        if _pull_sessions.get(getattr(self, "key", None)) is not self:
            return {"CANCELLED"}
        if (not self.valid_context(context) or event.type == "WINDOW_DEACTIVATE"
                or (event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS")):
            self.finish()
            return {"CANCELLED"}
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "LEFTMOUSE"}:
            self.gesture.update(event.mouse_x - self.region.x)
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            armed = self.gesture.release(event.mouse_x - self.region.x)
            targets = self.targets
            self.finish()
            if armed and physics_operators.remove_physics_targets(context.scene, targets):
                return {"FINISHED"}
            return {"CANCELLED"}
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def finish(self):
        if _pull_sessions.get(getattr(self, "key", None)) is self:
            del _pull_sessions[self.key]
        self.targets = ()
        super().finish()


def _draw_pull(context, bounds, blf, shader, batch):
    operator = _pull_sessions.get(quick_assign.region_key(context))
    if operator is None:
        return
    x, y, _, h, s = bounds
    gesture = operator.gesture
    armed = gesture.state == "ARMED"
    extension = (38 + 160*gesture.progress)*s
    left = x-extension
    color = (1.0, .12, .15, 1.0) if armed else (.80, .06, .09, .96)
    _rounded(shader, batch, left, y+10*s, extension+27*s, h-20*s, 17*s, color)
    # White silhouette matching the supplied trash SVG, painted beneath the pill.
    ix, iy = left+14*s, y+18*s
    _rounded(shader, batch, ix, iy, 12*s, 15*s, 2*s, _TEXT)
    _rounded(shader, batch, ix-2*s, iy+17*s, 16*s, 2*s, s, _TEXT)
    _rounded(shader, batch, ix+4*s, iy+20*s, 4*s, 2*s, s, _TEXT)
    if armed:
        text = "Release to detach" if len(operator.targets) == 1 else f"Release to detach {len(operator.targets)} objects"
        _label(blf, text, left, y+h+10*s, round(13*s), _TEXT)


class CLOTHNEXT_MT_floating_quality(bpy.types.Menu):
    bl_idname = "CLOTHNEXT_MT_floating_quality"
    bl_label = "Quality"

    def draw(self, context):
        pdrd = physics_operators.scene_has_pdrd(context.scene)
        ids = (physics_operators.PDRD_QUALITY_PRESET_OPERATOR_IDS if pdrd
               else physics_operators.QUALITY_PRESET_OPERATOR_IDS)
        for key in ("LOW", "MEDIUM", "HIGH", "EXTREME"):
            row = self.layout.row()
            row.enabled = key in ids and not shared_controller.snapshot().active
            if key in ids:
                row.operator(ids[key], text=key.title())


class CLOTHNEXT_OT_toggle_floating_ui(bpy.types.Operator):
    bl_idname = "clothnext.toggle_floating_ui"
    bl_label = "Show/Hide Cloth NeXt Toolbar"
    bl_description = "Temporarily show or hide the New Look viewport toolbar"

    def execute(self, context):
        global _toolbar_visible, _animation_from, _animation_start_time
        if not enabled(context):
            return {"CANCELLED"}
        _animation_from = _slide_fraction()
        _toolbar_visible = not _toolbar_visible
        quick_assign.cancel_all()
        _cancel_pulls()
        _animation_start_time = time.monotonic()
        if not bpy.app.timers.is_registered(_animation_tick):
            bpy.app.timers.register(_animation_tick, first_interval=1.0 / 60.0)
        _tag_redraw(context)
        return {"FINISHED"}


class CLOTHNEXT_GT_floating_simulation(bpy.types.GizmoGroup):
    bl_idname = "CLOTHNEXT_GT_floating_simulation"
    bl_label = "Cloth NeXt New Look"
    bl_space_type = "VIEW_3D"
    bl_region_type = "WINDOW"
    bl_options = {"PERSISTENT", "SCALE"}

    @classmethod
    def poll(cls, context):
        return visible(context) and _bounds(context) is not None

    def setup(self, context):
        self._buttons = []
        for operator in ("clothnext.set_cache_directory", "wm.call_menu",
                         "clothnext.bake", "clothnext.bake_cancel",
                         "clothnext.companion_open_logs", "clothnext.quick_assign",
                         "clothnext.pull_detach"):
            gizmo = self.gizmos.new("GIZMO_GT_button_2d")
            gizmo.icon = "BLANK1"
            gizmo.draw_options = set()
            gizmo.color = _SURFACE[:3]
            gizmo.alpha = 0.01
            gizmo.color_highlight = _CYAN[:3]
            gizmo.alpha_highlight = 0.30
            props = gizmo.target_set_operator(operator)
            if operator == "wm.call_menu":
                props.name = CLOTHNEXT_MT_floating_quality.bl_idname
            self._buttons.append(gizmo)
        self.refresh(context)

    def refresh(self, context):
        from mathutils import Matrix
        if not visible(context):
            for gizmo in self._buttons:
                gizmo.hide = True
            return
        bounds = _animated_bounds(context)
        if bounds is None:
            return
        x, y, _w, _h, s = bounds
        quality_width = _quality_width(context)
        model, snapshot, quality, _directory_ok, message = _state(context)
        ui = s / _LAYOUT_SCALE
        positions = ((91, 27), (126 + quality_width/2, 27),
                     (177 + quality_width, 27),
                     (177 + quality_width, 27),
                     ((_w+18*ui)/s, 27),
                     ((230 + quality_width)/2, 54 + 31*ui/s), (27, 27))
        for gizmo, (dx, dy) in zip(self._buttons, positions):
            gizmo.matrix_basis = Matrix.Translation((x+dx*s, y+dy*s, 0))
            gizmo.scale_basis = (12*ui if gizmo is self._buttons[4]
                                 else max(16*ui, 19*s))
        self._buttons[6].hide = snapshot.active
        self._buttons[6].alpha_highlight = 0.0
        self._buttons[0].hide = snapshot.active
        self._buttons[1].hide = snapshot.active
        self._buttons[1].color_highlight = (
            _DIR_MISSING if quality and quality.identifier == "EXTREME"
            else _CYAN)[:3]
        self._buttons[2].hide = snapshot.active or not (model and model.enabled)
        self._buttons[3].hide = not snapshot.active or not snapshot.can_cancel
        self._buttons[4].hide = not bool(message)
        self._buttons[5].hide = not bool(quick_assign.valid_roles(context))
        # Blender does not guarantee refresh() for each timer-driven redraw.
        # Keep hit targets at the same translated position as the painted bar.
        for gizmo in self._buttons:
            if gizmo.matrix_basis.translation.y + gizmo.scale_basis < 0:
                gizmo.hide = True

    def draw_prepare(self, context):
        self.refresh(context)


CLASSES = quick_assign.CLASSES + (CLOTHNEXT_OT_pull_detach, CLOTHNEXT_MT_floating_quality, CLOTHNEXT_OT_toggle_floating_ui,
           CLOTHNEXT_GT_floating_simulation)


def _tag_redraw(context):
    wm = getattr(context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in getattr(window.screen, "areas", ()):
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _pulse():
    quick_assign.prune_sessions()
    for operator in tuple(_pull_sessions.values()):
        try:
            with bpy.context.temp_override(window=operator.window, area=operator.area, region=operator.region):
                alive = operator.valid_context(bpy.context)
        except (ReferenceError, RuntimeError, AttributeError):
            alive = False
        if not alive:
            operator.finish()
    if enabled(bpy.context):
        _tag_redraw(bpy.context)
    return 0.5


def sync(context=None):
    global _handle, _toolbar_visible, _last_enabled, _slide
    if not _registered:
        return
    context = context or bpy.context
    active = enabled(context)
    if active and not _last_enabled:
        _stop_animation()
        _toolbar_visible = True
        _slide = 1.0
    elif not active:
        _stop_animation()
        quick_assign.cancel_all()
        _cancel_pulls()
    _last_enabled = active
    if active and _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw, (), "WINDOW", "POST_PIXEL")
        if not bpy.app.timers.is_registered(_pulse):
            bpy.app.timers.register(_pulse, first_interval=0.5)
    elif not active and _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
        if bpy.app.timers.is_registered(_pulse):
            bpy.app.timers.unregister(_pulse)
    _tag_redraw(context)


def register():
    global _registered, _keymap, _keymap_item
    _registered = True
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_post", None)
    if handlers is not None and _scene_loaded not in handlers:
        handlers.append(_scene_loaded)
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_pre", None)
    if handlers is not None and _scene_loading not in handlers:
        handlers.append(_scene_loading)
    kc = getattr(getattr(bpy.context.window_manager, "keyconfigs", None),
                 "addon", None)
    if kc is not None and _keymap_item is None:
        _keymap = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        _keymap_item = _keymap.keymap_items.new(
            CLOTHNEXT_OT_toggle_floating_ui.bl_idname, "F6", "PRESS")
    sync()


def unregister():
    global _registered, _handle, _keymap, _keymap_item, _last_enabled
    _registered = False
    quick_assign.cancel_all()
    _cancel_pulls()
    _stop_animation()
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_post", None)
    if handlers is not None and _scene_loaded in handlers:
        handlers.remove(_scene_loaded)
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_pre", None)
    if handlers is not None and _scene_loading in handlers:
        handlers.remove(_scene_loading)
    if _keymap is not None and _keymap_item is not None:
        _keymap.keymap_items.remove(_keymap_item)
    _keymap = _keymap_item = None
    _last_enabled = False
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    if bpy.app.timers.is_registered(_pulse):
        bpy.app.timers.unregister(_pulse)
    for image in _images.values():
        try:
            if image.name in bpy.data.images:
                bpy.data.images.remove(image)
        except ReferenceError:
            pass
    _images.clear()
    _tag_redraw(bpy.context)
