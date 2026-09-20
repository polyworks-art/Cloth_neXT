# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene-local shared contact settings and a selection-independent Collider picker."""
from __future__ import annotations
from dataclasses import dataclass
import time
import uuid
import bpy
from ..bake.controller import shared_controller
from . import validation_state

FIELDS = ("collider_motion", "surface_grip", "collision_gap", "surface_offset")
_sessions = {}
_handle = None
_installed = False
_repairing = False


def is_collider(obj):
    try:
        return (obj is not None and obj.type == "MESH" and obj.cloth_next.enabled
                and obj.cloth_next.role == "COLLIDER")
    except (ReferenceError, AttributeError):
        return False


def group_for(obj, scene=None):
    if not is_collider(obj):
        return None
    identifier = getattr(obj.cloth_next, "collision_settings_group_id", "")
    scenes = (scene,) if scene is not None else getattr(obj, "users_scene", ())
    for owner in scenes:
        if owner is not None:
            for group in getattr(owner, "cloth_next_collision_groups", ()):
                if group.uuid == identifier and identifier:
                    return group
    return None


@dataclass(frozen=True)
class LocalSettings:
    settings: object

    @property
    def collider_motion(self):
        return getattr(self.settings, "collider_motion", "STATIC")

    def __getattr__(self, name):
        return getattr(self.settings.collision, name)


def effective_collider_settings(obj, scene=None):
    """The authoritative shared/local resolver. It never repairs data in drawing."""
    return group_for(obj, scene) or LocalSettings(obj.cloth_next)


def effective_from_settings(settings, scene=None):
    obj = getattr(settings, "id_data", None)
    return effective_collider_settings(obj, scene) if is_collider(obj) else LocalSettings(settings)


def members(scene, group):
    if not group.uuid:
        return ()
    return tuple(obj for obj in scene.objects if is_collider(obj)
                 and getattr(obj.cloth_next, "collision_settings_group_id", "") == group.uuid)


def copy_local(obj, values):
    obj.cloth_next.collider_motion = values.collider_motion
    for field in FIELDS[1:]:
        setattr(obj.cloth_next.collision, field, getattr(values, field))


def repair_scene(scene):
    """Dissolve singleton/orphan groups and clear stale references."""
    global _repairing
    if _repairing or shared_controller.snapshot().active:
        return
    _repairing = True
    try:
        groups = getattr(scene, "cloth_next_collision_groups", ())
        group_map = {group.uuid: group for group in groups}
        valid_ids = set(group_map)
        for obj in scene.objects:
            settings = getattr(obj, "cloth_next", None)
            identifier = getattr(settings, "collision_settings_group_id", "")
            if identifier and (identifier not in valid_ids or not is_collider(obj)):
                if identifier in group_map and getattr(obj, "is_editable", True):
                    copy_local(obj, group_map[identifier])
                settings.collision_settings_group_id = ""
        for index in reversed(range(len(groups))):
            group = groups[index]
            remaining = members(scene, group)
            if len(remaining) < 2:
                for obj in remaining:
                    copy_local(obj, group)
                    obj.cloth_next.collision_settings_group_id = ""
                groups.remove(index)
    finally:
        _repairing = False


def contains(collection, obj):
    if obj is None:
        return False
    try:
        return collection.get(obj.name) is obj
    except AttributeError:
        return any(obj is item for item in collection)
    except ReferenceError:
        return False


def editable(scene, obj):
    # One object property cannot express distinct membership in multiple scenes.
    return (is_collider(obj) and contains(scene.objects, obj)
            and not getattr(obj, "library", None)
            and getattr(obj, "is_editable", True)
            and len(getattr(obj, "users_scene", (scene,))) <= 1)


def classify(scene, view_layer, source, obj):
    if obj is source:
        return "SAME_GROUP"
    if (not editable(scene, obj)
            or not contains(view_layer.objects, obj)):
        return "INVALID"
    try:
        if not obj.visible_get(view_layer=view_layer):
            return "INVALID"
    except AttributeError:
        pass
    source_group, target_group = group_for(source, scene), group_for(obj, scene)
    if source_group is not None and target_group is not None and source_group.uuid == target_group.uuid:
        return "SAME_GROUP"
    return "OTHER_GROUP" if target_group is not None else "AVAILABLE"


def link(scene, source, target, *, allow_move=False):
    if (shared_controller.snapshot().active or source is target
            or not editable(scene, source) or not editable(scene, target)):
        return False
    old_group = group_for(target, scene)
    group = group_for(source, scene)
    if old_group is not None:
        if group is not None and old_group.uuid == group.uuid:
            return False
        if not allow_move:
            return False
    if group is None:
        values = effective_collider_settings(source, scene)
        group = scene.cloth_next_collision_groups.add()
        group.uuid = str(uuid.uuid4())
        for field in FIELDS:
            setattr(group, field, getattr(values, field))
        source.cloth_next.collision_settings_group_id = group.uuid
    target.cloth_next.collision_settings_group_id = group.uuid
    repair_scene(scene)
    validation_state.mark_all_settings_dirty()
    return True


def unlink(scene, obj):
    if shared_controller.snapshot().active or not editable(scene, obj):
        return False
    group = group_for(obj, scene)
    if group is None:
        return False
    copy_local(obj, group)
    obj.cloth_next.collision_settings_group_id = ""
    repair_scene(scene)
    validation_state.mark_all_settings_dirty()
    return True


class CLOTHNEXT_OT_shared_collision_info(bpy.types.Operator):
    bl_idname = "clothnext.shared_collision_info"
    bl_label = "Shared Collision Settings"
    bl_options = {"INTERNAL"}
    count: bpy.props.IntProperty(default=0, options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        return f"Shared between {properties.count} Colliders. Motion, Friction, Gap and Offset share one settings source."

    def execute(self, context):
        return {"FINISHED"}


class CLOTHNEXT_OT_link_collider(bpy.types.Operator):
    bl_idname = "clothnext.link_collider"
    bl_label = "Move to this group?"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    source_name: bpy.props.StringProperty(options={"HIDDEN"})
    source_pointer: bpy.props.StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    target_pointer: bpy.props.StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    source_group: bpy.props.StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    target_group: bpy.props.StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    target_name: bpy.props.StringProperty(options={"HIDDEN"})
    allow_move: bpy.props.BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def draw(self, context):
        self.layout.label(text="Collider already uses linked Collision Settings.")
        self.layout.label(text="Move it to this group?")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, confirm_text="Move")

    def execute(self, context):
        source = context.scene.objects.get(self.source_name)
        target = context.scene.objects.get(self.target_name)
        if self.source_pointer:
            if (source is None or target is None or str(source.as_pointer()) != self.source_pointer
                    or str(target.as_pointer()) != self.target_pointer
                    or source.cloth_next.collision_settings_group_id != self.source_group
                    or target.cloth_next.collision_settings_group_id != self.target_group):
                return {"CANCELLED"}
        if not link(context.scene, source, target, allow_move=self.allow_move):
            return {"CANCELLED"}
        return {"FINISHED"}


class CLOTHNEXT_OT_unlink_collider(bpy.types.Operator):
    bl_idname = "clothnext.unlink_collider"
    bl_label = "Unlink Collider"
    bl_description = "Keep current shared values and make this Collider independent"
    bl_options = {"REGISTER", "UNDO"}
    target_name: bpy.props.StringProperty(options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return not shared_controller.snapshot().active

    def execute(self, context):
        obj = context.scene.objects.get(self.target_name)
        return {"FINISHED"} if unlink(context.scene, obj) else {"CANCELLED"}


@dataclass
class PickState:
    hovered: object = None
    classification: str = "INVALID"
    feedback: str = ""
    feedback_until: float = 0.

    def update(self, obj, classification):
        self.hovered = obj
        self.classification = classification

    def click(self, now):
        if self.classification in {"AVAILABLE", "OTHER_GROUP"}:
            return self.classification
        self.feedback = "Already in this group" if self.classification == "SAME_GROUP" else "Not a Cloth NeXt Collider"
        self.feedback_until = now + 1.5
        return None


def viewport_at(window, event):
    for area in window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        for region in area.regions:
            if (region.type == "WINDOW" and region.x <= event.mouse_x < region.x+region.width
                    and region.y <= event.mouse_y < region.y+region.height):
                return area, region
    return None, None


def object_at(context, event, region):
    from bpy_extras import view3d_utils
    point = (event.mouse_x-region.x, event.mouse_y-region.y)
    origin = view3d_utils.region_2d_to_origin_3d(region, context.region_data, point)
    direction = view3d_utils.region_2d_to_vector_3d(region, context.region_data, point)
    hit, _, _, _, obj, _ = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
    return getattr(obj, "original", obj) if hit else None


def redraw(window):
    try:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    except ReferenceError:
        pass


def _draw_overlay():
    context = bpy.context
    operator = _sessions.get(context.window.as_pointer()) if context.window else None
    if operator is None or context.scene != operator.scene or context.view_layer != operator.view_layer:
        return
    import gpu
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    try:
        for obj, kind, batch in operator.batches:
            hovered = obj is operator.state.hovered
            color = (1., .56, .12) if kind == "OTHER_GROUP" else (.06, .66, .85)
            shader.bind()
            shader.uniform_float("color", (*color, .95 if hovered else .28))
            gpu.state.line_width_set(2.5 if hovered else 1.)
            batch.draw(shader)
    finally:
        gpu.state.line_width_set(1.)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")


def _draw_feedback():
    context = bpy.context
    operator = _sessions.get(context.window.as_pointer()) if context.window else None
    if operator is None or not operator.state.feedback or time.monotonic() > operator.state.feedback_until:
        return
    import blf
    from . import floating_simulation as floating
    s = floating._scale(context) / floating._LAYOUT_SCALE
    import gpu
    try:
        floating._label(blf, operator.state.feedback, 24*s, 40*s, round(14*s), floating._TEXT)
    finally:
        gpu.state.blend_set("NONE")


_feedback_handle = None


def _stop_overlay():
    global _handle, _feedback_handle
    if _sessions:
        return
    for name in ("_handle", "_feedback_handle"):
        handle = globals()[name]
        if handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
            globals()[name] = None
    if bpy.app.timers.is_registered(_prune):
        bpy.app.timers.unregister(_prune)


def _prune():
    for operator in tuple(_sessions.values()):
        if not operator.alive():
            operator.finish()
        else:
            if operator.needs_rebuild:
                try:
                    with bpy.context.temp_override(window=operator.window):
                        operator.rebuild(bpy.context)
                except (ReferenceError, RuntimeError):
                    operator.finish()
            redraw(operator.window)
    return .15 if _sessions else None


class CLOTHNEXT_OT_pick_collider(bpy.types.Operator):
    bl_idname = "clothnext.pick_collider"
    bl_label = "Add Linked Collider"
    bl_description = "Pick a Collider without changing selection or the active object"
    bl_options = {"INTERNAL", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return (getattr(context, "mode", "OBJECT") == "OBJECT"
                and context.scene is not None and editable(context.scene, context.object)
                and not shared_controller.snapshot().active)

    def invoke(self, context, event):
        global _handle, _feedback_handle
        if not self.poll(context) or context.window.as_pointer() in _sessions:
            return {"CANCELLED"}
        self.window, self.screen, self.workspace = context.window, context.window.screen, context.window.workspace
        self.origin_area = context.area
        self.scene, self.view_layer, self.source = context.scene, context.view_layer, context.object
        self.wm = context.window_manager
        self._finished = False
        self.state, self.batches = PickState(), []
        self.viewport_area = None
        self.needs_rebuild = True
        self.key = self.window.as_pointer()
        _sessions[self.key] = self
        try:
            if _handle is None:
                _handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), "WINDOW", "POST_VIEW")
                _feedback_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_feedback, (), "WINDOW", "POST_PIXEL")
            if not bpy.app.timers.is_registered(_prune):
                bpy.app.timers.register(_prune, first_interval=.15)
            self.rebuild(context)
            self.wm.modal_handler_add(self)
            self.window.cursor_modal_set("EYEDROPPER")
        except Exception:
            self.finish()
            raise
        redraw(self.window)
        return {"RUNNING_MODAL"}

    def alive(self):
        try:
            return (_installed and self.window in tuple(self.wm.windows)
                    and self.window.screen == self.screen and self.window.workspace == self.workspace
                    and self.window.scene == self.scene and self.window.view_layer == self.view_layer
                    and self.origin_area in tuple(self.screen.areas)
                    and (self.viewport_area is None or self.viewport_area in tuple(self.screen.areas))
                    and any(a.type == "VIEW_3D" for a in self.screen.areas)
                    and editable(self.scene, self.source) and not shared_controller.snapshot().active)
        except (ReferenceError, AttributeError):
            return False

    def rebuild(self, context):
        from gpu_extras.batch import batch_for_shader
        import gpu
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        depsgraph = context.evaluated_depsgraph_get()
        batches = []
        for obj in self.view_layer.objects:
            kind = classify(self.scene, self.view_layer, self.source, obj)
            if kind not in {"AVAILABLE", "OTHER_GROUP"}:
                continue
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.data
            positions = [tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices]
            indices = [tuple(edge.vertices) for edge in mesh.edges]
            if indices:
                batch = batch_for_shader(shader, "LINES", {"pos": positions}, indices=indices)
                batches.append((obj, kind, batch))
        self.batches = batches
        self.needs_rebuild = False

    def modal(self, context, event):
        if _sessions.get(getattr(self, "key", None)) is not self:
            return {"CANCELLED"}
        if not self.alive() or event.type == "WINDOW_DEACTIVATE" or (event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS"):
            self.finish()
            return {"CANCELLED"}
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "LEFTMOUSE"}:
            area, region = viewport_at(self.window, event)
            target = None
            if area is not None:
                self.viewport_area = area
                with context.temp_override(window=self.window, area=area, region=region):
                    target = object_at(context, event, region)
                    if self.needs_rebuild:
                        self.rebuild(context)
            self.state.update(target, classify(self.scene, self.view_layer, self.source, target))
            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                kind = self.state.click(time.monotonic())
                if kind is not None:
                    source_name, target_name = self.source.name, target.name
                    identity = dict(source_pointer=str(self.source.as_pointer()), target_pointer=str(target.as_pointer()),
                                    source_group=self.source.cloth_next.collision_settings_group_id,
                                    target_group=target.cloth_next.collision_settings_group_id)
                    self.finish()
                    result = bpy.ops.clothnext.link_collider(
                        "INVOKE_DEFAULT" if kind == "OTHER_GROUP" else "EXEC_DEFAULT", True,
                        source_name=source_name, target_name=target_name, allow_move=kind == "OTHER_GROUP", **identity)
                    return {"FINISHED"} if "CANCELLED" not in result else {"CANCELLED"}
            redraw(self.window)
        return {"RUNNING_MODAL"}

    def finish(self):
        if getattr(self, "_finished", False):
            return
        self._finished = True
        if _sessions.get(getattr(self, "key", None)) is self:
            del _sessions[self.key]
        self.batches = []
        self.state = PickState()
        self.source = None
        try:
            self.window.cursor_modal_restore()
            redraw(self.window)
        except (ReferenceError, AttributeError):
            pass
        _stop_overlay()

    def cancel(self, context):
        self.finish()


def _repair_tick():
    if not _installed:
        return None
    for scene in bpy.data.scenes:
        repair_scene(scene)
    return None


def _schedule_repair(*args):
    for operator in _sessions.values():
        operator.needs_rebuild = True
    if _installed and not _repairing and not bpy.app.timers.is_registered(_repair_tick):
        bpy.app.timers.register(_repair_tick, first_interval=0.)


_persistent = getattr(getattr(bpy.app, "handlers", None), "persistent", lambda fn: fn)
_schedule_repair = _persistent(_schedule_repair)


def register():
    global _installed
    _installed = True
    for name in ("load_post", "undo_post", "redo_post"):
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and _schedule_repair not in handlers:
            handlers.append(_schedule_repair)
    for name in ("load_pre", "undo_pre"):
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and _cancel_sessions not in handlers:
            handlers.append(_cancel_sessions)
    validation_state.add_depsgraph_observer(_on_depsgraph)
    _schedule_repair()


@_persistent
def _on_depsgraph(scene, depsgraph=None):
    for operator in _sessions.values():
        operator.needs_rebuild = True
    if getattr(scene, "cloth_next_collision_groups", ()):
        _schedule_repair()


@_persistent
def _cancel_sessions(*args):
    for operator in tuple(_sessions.values()):
        operator.finish()


def unregister():
    global _installed
    _installed = False
    for operator in tuple(_sessions.values()):
        operator.finish()
    _stop_overlay()
    if bpy.app.timers.is_registered(_repair_tick):
        bpy.app.timers.unregister(_repair_tick)
    for name in ("load_post", "undo_post", "redo_post"):
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and _schedule_repair in handlers:
            handlers.remove(_schedule_repair)
    for name in ("load_pre", "undo_pre"):
        handlers = getattr(bpy.app.handlers, name, None)
        if handlers is not None and _cancel_sessions in handlers:
            handlers.remove(_cancel_sessions)
    validation_state.remove_depsgraph_observer(_on_depsgraph)


CLASSES = (CLOTHNEXT_OT_shared_collision_info, CLOTHNEXT_OT_link_collider, CLOTHNEXT_OT_unlink_collider, CLOTHNEXT_OT_pick_collider)
