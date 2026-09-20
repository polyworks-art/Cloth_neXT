# SPDX-License-Identifier: GPL-3.0-or-later
"""Quick Assign adapter. The existing toolbar owns rendering and gizmos."""
import math
import time

import bpy

from ..quick_assign import Gesture, ROLE_ORDER, make_layout, fan_progress
from ..bake.controller import shared_controller
from . import object_properties

# Only live gestures, keyed by their originating window/region. No idle timer.
_sessions = {}
ROLE_ICONS = {"CLOTH": "quick_cloth", "ROD": "quick_rod",
              "RIGID_BODY": "quick_rigid_body", "SOFT_BODY": "quick_soft_body",
              "COLLIDER": "quick_collider"}


def region_key(context):
    return context.window.as_pointer(), context.region.as_pointer()


def valid_roles(context):
    obj = getattr(context, "active_object", None)
    if (getattr(context, "mode", "OBJECT") != "OBJECT"
            or shared_controller.snapshot().active
            or getattr(obj, "cloth_next", None) is None):
        return ()
    if obj.type == "CURVE":
        return ("ROD",)
    return tuple(role for role in ROLE_ORDER if role != "ROD") if obj.type == "MESH" else ()


def current_role(context):
    objects = getattr(context, "selected_objects", ()) or (context.active_object,)
    roles = {getattr(obj.cloth_next, "role", None)
             if getattr(getattr(obj, "cloth_next", None), "enabled", False)
             else None for obj in objects}
    return next(iter(roles)) if len(roles) == 1 else None


class CLOTHNEXT_OT_quick_assign_role(bpy.types.Operator):
    """Reuse the existing active-object setup actions in one undo step."""
    bl_idname = "clothnext.quick_assign_role"
    bl_label = "Quick Assign Cloth NeXt Role"
    bl_options = {"UNDO", "INTERNAL"}

    role: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return bool(valid_roles(context))

    def execute(self, context):
        if self.role not in valid_roles(context):
            return {"CANCELLED"}
        # Nested operators do not push separate undo steps. They retain all
        # cache inheritance, persistent ID and quality-remapping behavior.
        if not context.active_object.cloth_next.enabled:
            result = bpy.ops.clothnext.add_physics("EXEC_DEFAULT", False)
            if "FINISHED" not in result:
                return {"CANCELLED"}
        return bpy.ops.clothnext.set_object_type("EXEC_DEFAULT", False, role=self.role)


def button_bounds(context):
    from . import floating_simulation as floating
    bounds = floating._animated_bounds(context)
    if bounds is None:
        return None
    x, y, width, height, scale = bounds
    ui = scale / floating._LAYOUT_SCALE
    # Separate floating Quick Button, centered above the compact toolbar.
    return (x + width / 2, y + height + 31 * ui, 17 * ui, ui)


def session(context):
    if context.window is None or context.region is None:
        return None
    return _sessions.get(region_key(context))


def cancel_all():
    for operator in tuple(_sessions.values()):
        operator.finish()


def prune_sessions():
    """A closed window may stop delivering its modal timer events."""
    for operator in tuple(_sessions.values()):
        try:
            alive = (operator.window in tuple(bpy.context.window_manager.windows)
                     and operator.window.screen == operator.screen
                     and operator.window.workspace == operator.workspace
                     and operator.area in tuple(operator.screen.areas)
                     and operator.region in tuple(operator.area.regions)
                     and operator.area.type == "VIEW_3D")
        except (ReferenceError, AttributeError):
            alive = False
        if not alive:
            operator.finish()


class CLOTHNEXT_OT_quick_assign(bpy.types.Operator):
    bl_idname = "clothnext.quick_assign"
    bl_label = "Quick Assign"
    bl_description = "Hold or drag to assign a Cloth NeXt role to the active object"
    bl_options = {"INTERNAL", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        from . import floating_simulation as floating
        return (getattr(context.area, "type", None) == "VIEW_3D"
                and getattr(context.region, "type", None) == "WINDOW"
                and floating.visible(context) and bool(valid_roles(context)))

    def invoke(self, context, event):
        from . import floating_simulation as floating
        bounds = button_bounds(context)
        if (event.type != "LEFTMOUSE" or event.value != "PRESS"
                or not self.poll(context) or bounds is None
                or floating._animation_start_time is not None):
            return {"CANCELLED"}
        self.key = region_key(context)
        if self.key in _sessions:
            return {"CANCELLED"}
        self.window, self.area, self.region = context.window, context.area, context.region
        self.screen, self.workspace = context.window.screen, context.window.workspace
        self.scene, self.view_layer = context.scene, context.view_layer
        self.active = context.active_object
        self.selection = tuple(obj.as_pointer() for obj in context.selected_objects)
        self.wm = context.window_manager
        self.dimensions = (self.region.width, self.region.height)
        self.scale = bounds[3]
        self.gesture = Gesture(make_layout(bounds[:2], *self.dimensions, self.scale),
                               time.monotonic())
        self.current = current_role(context)
        self.allowed = valid_roles(context)
        self.timer = None
        _sessions[self.key] = self
        try:
            self.timer = self.wm.event_timer_add(.025, window=self.window)
            self.wm.modal_handler_add(self)
        except Exception:
            self.finish()
            raise
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def valid_context(self, context):
        from . import floating_simulation as floating
        try:
            bounds = button_bounds(context)
            return (floating._registered and floating.visible(context)
                    and floating._animation_start_time is None
                    and context.window == self.window
                    and self.window in tuple(self.wm.windows)
                    and self.window.screen == self.screen
                    and self.window.workspace == self.workspace
                    and self.area in tuple(self.screen.areas)
                    and self.area.type == "VIEW_3D"
                    and self.region in tuple(self.area.regions)
                    and context.area == self.area and context.region == self.region
                    and context.scene == self.scene and context.view_layer == self.view_layer
                    and context.active_object == self.active
                    and tuple(obj.as_pointer() for obj in context.selected_objects) == self.selection
                    and (self.region.width, self.region.height) == self.dimensions
                    and bounds is not None and bounds[3] == self.scale
                    and bounds[:2] == self.gesture.layout.center
                    and valid_roles(context) == self.allowed)
        except (ReferenceError, AttributeError):
            return False

    def modal(self, context, event):
        if _sessions.get(getattr(self, "key", None)) is not self:
            return {"CANCELLED"}
        if (not self.valid_context(context) or event.type == "WINDOW_DEACTIVATE"
                or (event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS")):
            self.finish()
            return {"CANCELLED"}
        now = time.monotonic()
        point = self.gesture.position
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "LEFTMOUSE"}:
            point = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            role = self.gesture.release(point, now, self.allowed)
            self.finish()
            if role is not None:
                bpy.ops.clothnext.quick_assign_role("EXEC_DEFAULT", True, role=role)
                return {"FINISHED"}
            return {"CANCELLED"}
        self.gesture.update(point, now, self.allowed)
        self.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def finish(self):
        """Idempotent teardown, also used before file load and unregister."""
        if hasattr(self, "gesture"):
            self.gesture.cancel()
        key = getattr(self, "key", None)
        if _sessions.get(key) is self:
            del _sessions[key]
        if getattr(self, "timer", None) is not None:
            try:
                self.wm.event_timer_remove(self.timer)
            except (ReferenceError, RuntimeError):
                pass
            self.timer = None
        try:
            self.area.tag_redraw()
        except (ReferenceError, AttributeError):
            pass

    def cancel(self, context):
        self.finish()


def _draw_sector(gesture, fade, gpu, batch):
    from . import floating_simulation as floating
    index = gesture.layout.roles.index(gesture.target)
    vertices, opacity, triangles = gesture.layout.sector_mesh(index)
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    colors = [(*floating._CYAN[:3], alpha*fade) for alpha in opacity]
    shader.bind()
    batch(shader, "TRIS", {"pos": vertices, "color": colors},
          indices=triangles).draw(shader)


def _draw_sector_label(gesture, label, blf, gpu):
    """Read outward along the wedge; flip leftward labels to stay upright."""
    layout = gesture.layout
    index = layout.roles.index(gesture.target)
    angle = layout.label_angle(index)
    size = max(1, round(10*layout.scale))
    blf.size(0, size)
    width, height = blf.dimensions(0, label)
    if width > 40*layout.scale:
        blf.size(0, max(1, size*40*layout.scale/width))
        width, height = blf.dimensions(0, label)
    x, y = layout.point(index, 42*layout.scale)
    # Rotate the baseline offset together with the text, not just its origin.
    x += -width/2*math.cos(angle) + height/2*math.sin(angle)
    y += -width/2*math.sin(angle) - height/2*math.cos(angle)
    blf.enable(0, blf.ROTATION)
    try:
        blf.rotation(0, angle)
        blf.color(0, .035, .055, .065, 1.)
        blf.position(0, x, y, 0)
        blf.draw(0, label)
        # A tiny second stroke gives the compact radial caption the stronger
        # weight of the reference without loading another font at runtime.
        blf.position(0, x+.3*layout.scale*math.cos(angle),
                     y+.3*layout.scale*math.sin(angle), 0)
        blf.draw(0, label)
    finally:
        blf.rotation(0, 0.)
        blf.disable(0, blf.ROTATION)
        gpu.state.blend_set("ALPHA")


def draw(context, blf, gpu, batch, shader):
    from . import floating_simulation as floating
    bounds = button_bounds(context)
    if bounds is None:
        return
    # BLF can reset blending after the toolbar's diagnostic text.
    gpu.state.blend_set("ALPHA")
    cx, cy, radius, scale = bounds
    operator = session(context)
    gesture = operator.gesture if operator else None
    opened = gesture is not None and gesture.opened is not None
    elapsed = time.monotonic() - gesture.opened if opened else 0.
    fade = fan_progress(elapsed, 0) if opened else 0.
    if opened and gesture.target:
        _draw_sector(gesture, fade, gpu, batch)
    floating._rounded(shader, batch, cx-radius, cy-radius, radius*2, radius*2,
                      radius, floating._BG)
    floating._asset_icon(gpu, batch, "add", cx-14*scale, cy-14*scale, 28*scale)
    if not opened:
        return
    bubble_scale = gesture.layout.scale
    labels = {role: label for role, label, _ in object_properties.ROLE_ITEMS}
    labels["RIGID_BODY"] = "RBD"
    for i, role in enumerate(gesture.layout.roles):
        progress = fan_progress(elapsed, i)
        if progress <= 0.:
            continue
        target_x, target_y = gesture.layout.point(i)
        origin_x, origin_y = gesture.layout.center
        x = origin_x + (target_x - origin_x) * progress
        y = origin_y + (target_y - origin_y) * progress
        active = gesture.target == role
        reveal_scale = .65 + .35 * progress
        r = gesture.layout.bubble_radius * (1.12 if active else 1.) * reveal_scale
        color = floating._CYAN if active else floating._BG
        if role == operator.current:
            floating._rounded(shader, batch, x-r-2*bubble_scale, y-r-2*bubble_scale,
                              2*r+4*bubble_scale, 2*r+4*bubble_scale, r+2*bubble_scale,
                              (*floating._MUTED[:3], progress))
        floating._rounded(shader, batch, x-r, y-r, 2*r, 2*r, r,
                          (*color[:3], color[3]*progress))
        size = 23*bubble_scale * reveal_scale
        floating._asset_icon(gpu, batch, ROLE_ICONS[role],
                             x-size/2, y-size/2, size)
        if role not in operator.allowed:
            floating._rounded(shader, batch, x-r, y-r, 2*r, 2*r, r,
                              (.1, .11, .12, .65))
    if gesture.target:
        _draw_sector_label(gesture, labels[gesture.target].upper(), blf, gpu)


CLASSES = (CLOTHNEXT_OT_quick_assign_role, CLOTHNEXT_OT_quick_assign)
