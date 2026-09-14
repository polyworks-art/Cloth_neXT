# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional viewport presentation for the existing Simulation workflow.

Blender gizmos own the clickable areas; the draw handler only paints the bar.
No modal event handler or simulation state is stored here.
"""
from __future__ import annotations

import os
from pathlib import Path

import bpy

from ..bake.controller import shared_controller
from ..solver_quality import matching_quality_preset
from . import object_properties, physics_operators, physics_ui
from .addon_identity import addon_preferences

_handle = None
_registered = False
_images = {}
_toolbar_visible = True
_last_enabled = False
_keymap = None
_keymap_item = None
_LAYOUT_SCALE = 0.65
_persistent = getattr(getattr(bpy.app, "handlers", None),
                      "persistent", lambda fn: fn)
_BG = (0.105, 0.119, 0.130, 0.94)  # charcoal
_SURFACE = (0.17, 0.19, 0.21, 0.97)
_CYAN = (0.06, 0.66, 0.85, 1.0)
_BLUE = (0.07, 0.43, 0.72, 1.0)
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
    return enabled(context) and _toolbar_visible


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
    if region.width < width + 24 * scale or region.height < height + 48 * scale:
        return None
    return ((region.width - width) / 2, 28 * scale, width, height, scale)


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
def _scene_loaded(_dummy):
    # Loading a .blend frees images from the old Main, invalidating RNA refs.
    _images.clear()
    if _registered:
        _tag_redraw(bpy.context)


def _draw():
    context = bpy.context
    if not visible(context):
        return
    bounds = _bounds(context)
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
            x+bake_x*s, y+23*s, 82*s, round(14*s),
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
            ix, iy = x + 9*ui, y+h+13*ui
            color = _DIR_MISSING if error else (1.0, 1.0, 1.0, 1.0)
            _rounded(shader, batch_for_shader,
                     ix-7*ui, iy-7*ui, 14*ui, 14*ui, 7*ui,
                     _DIR_MISSING if error else _BLUE)
            _centered_glyph(blf, "!" if error else "i", ix, iy,
                            round(10*ui), (1.0, 1.0, 1.0, 1.0))
            available = max(1, context.region.width - (ix+27*ui))
            _label(blf, str(message)[:int(available / (6*ui))],
                   ix+15*ui, iy-4*ui, round(11*ui), color)
    finally:
        gpu.state.blend_set("NONE")


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
        global _toolbar_visible
        if not enabled(context):
            return {"CANCELLED"}
        _toolbar_visible = not _toolbar_visible
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
                         "clothnext.companion_open_logs"):
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
        bounds = _bounds(context)
        if bounds is None:
            return
        x, y, _w, _h, s = bounds
        quality_width = _quality_width(context)
        model, snapshot, quality, _directory_ok, message = _state(context)
        ui = s / _LAYOUT_SCALE
        positions = ((91, 27), (126 + quality_width/2, 27),
                     (177 + quality_width, 27),
                     (177 + quality_width, 27),
                     (9*ui/s, 54 + 13*ui/s))
        for gizmo, (dx, dy) in zip(self._buttons, positions):
            gizmo.matrix_basis = Matrix.Translation((x+dx*s, y+dy*s, 0))
            gizmo.scale_basis = (12*ui if gizmo is self._buttons[4]
                                 else max(16*ui, 19*s))
        self._buttons[0].hide = snapshot.active
        self._buttons[1].hide = snapshot.active
        self._buttons[1].color_highlight = (
            _DIR_MISSING if quality and quality.identifier == "EXTREME"
            else _CYAN)[:3]
        self._buttons[2].hide = snapshot.active or not (model and model.enabled)
        self._buttons[3].hide = not snapshot.active or not snapshot.can_cancel
        self._buttons[4].hide = not bool(message)


CLASSES = (CLOTHNEXT_MT_floating_quality, CLOTHNEXT_OT_toggle_floating_ui,
           CLOTHNEXT_GT_floating_simulation)


def _tag_redraw(context):
    wm = getattr(context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in getattr(window.screen, "areas", ()):
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _pulse():
    if enabled(bpy.context):
        _tag_redraw(bpy.context)
    return 0.5


def sync(context=None):
    global _handle, _toolbar_visible, _last_enabled
    if not _registered:
        return
    context = context or bpy.context
    active = enabled(context)
    if active and not _last_enabled:
        _toolbar_visible = True
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
    handlers = getattr(getattr(bpy.app, "handlers", None), "load_post", None)
    if handlers is not None and _scene_loaded in handlers:
        handlers.remove(_scene_loaded)
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
