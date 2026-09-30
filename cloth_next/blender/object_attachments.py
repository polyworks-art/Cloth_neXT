# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent Cloth/Soft-Body object attachments and viewport preview."""

from __future__ import annotations

import hashlib
import time
import uuid

import bpy

from .. import export_identity
from ..attachments import (AttachmentError, AttachmentPoint, DEFAULT_STIFFNESS,
                           ObjectAttachment, SUPPORTED_ROLES, VertexSearch,
                           closest_surface_point, topology_fingerprint,
                           shortest_mesh_path, topology_vertex_mapping,
                           validate_attachment)
from ..bake.controller import shared_controller
from . import validation_state

try:
    import bmesh
except ImportError:  # pragma: no cover - available inside Blender
    bmesh = None


class CLOTHNEXT_PG_attachment_point(bpy.types.PropertyGroup):
    source_index: bpy.props.IntProperty(default=0, min=0)
    target_triangle: bpy.props.IntVectorProperty(size=3, default=(0, 0, 0))
    target_weights: bpy.props.FloatVectorProperty(
        size=3, default=(1.0, 0.0, 0.0))
    source_point: bpy.props.FloatVectorProperty(size=3)
    target_point: bpy.props.FloatVectorProperty(size=3)


class CLOTHNEXT_PG_attachment_vertex(bpy.types.PropertyGroup):
    index: bpy.props.IntProperty(default=0, min=0)


def _eligible_object(_self, obj):
    settings = getattr(obj, "cloth_next", None)
    return bool(obj and getattr(obj, "type", "") == "MESH" and settings
                and settings.enabled and settings.role in SUPPORTED_ROLES)


def _group_changed(item, context):
    validation_state.mark_all_settings_dirty()
    if not item.use_vertex_groups:
        return
    item.needs_rebuild = True
    item.status_message = "Selection changed. Bind vertex groups to update the attachment."


def _settings_changed(_item, _context):
    validation_state.mark_all_settings_dirty()


class CLOTHNEXT_PG_object_attachment(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(default="")
    name: bpy.props.StringProperty(default="Object Attachment")
    enabled: bpy.props.BoolProperty(
        default=True, update=_settings_changed)
    stiffness: bpy.props.FloatProperty(
        name="Strength", default=DEFAULT_STIFFNESS, min=0.0,
        update=_settings_changed)
    source_persistent_id: bpy.props.StringProperty(default="")
    target_persistent_id: bpy.props.StringProperty(default="")
    source_name: bpy.props.StringProperty(default="")
    target_name: bpy.props.StringProperty(default="")
    source_role: bpy.props.StringProperty(default="")
    target_role: bpy.props.StringProperty(default="")
    source_topology: bpy.props.StringProperty(default="")
    target_topology: bpy.props.StringProperty(default="")
    needs_rebuild: bpy.props.BoolProperty(default=False)
    status_message: bpy.props.StringProperty(default="")
    show_overlay: bpy.props.BoolProperty(default=True)
    points: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_attachment_point)
    source_vertices: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_attachment_vertex)
    target_vertices: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_attachment_vertex)
    use_vertex_groups: bpy.props.BoolProperty(default=False)
    group_signature: bpy.props.StringProperty(default="")
    target_object: bpy.props.PointerProperty(
        name="Target Object", type=bpy.types.Object, poll=_eligible_object,
        update=_group_changed)
    source_group: bpy.props.StringProperty(
        name="Vertex Group 1", description="Vertices on the source object to attach",
        update=_group_changed)
    target_group: bpy.props.StringProperty(
        name="Vertex Group 2", description="Target vertices to attach to; nearest vertex is used",
        update=_group_changed)


def _objects_by_identity(scene):
    return {
        str(getattr(obj.cloth_next, "persistent_export_id", "")): obj
        for obj in scene.objects
        if getattr(obj, "cloth_next", None)
    }


def _mesh_triangles(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    vertices = tuple(tuple(obj.matrix_world @ vertex.co)
                     for vertex in mesh.vertices)
    triangles = tuple(tuple(int(index) for index in tri.vertices)
                      for tri in mesh.loop_triangles)
    return vertices, triangles


def _selected_indices(item, side):
    stored = tuple(int(row.index) for row in getattr(item, f"{side}_vertices", ()))
    if stored:
        return stored
    if side == "source":
        return tuple(sorted({int(point.source_index) for point in item.points}))
    result = set()
    for point in item.points:
        weights = tuple(map(float, point.target_weights))
        triangle = tuple(map(int, point.target_triangle))
        result.add(triangle[max(range(3), key=lambda index: weights[index])])
    return tuple(sorted(result))


def _fill_indices(collection, indices):
    collection.clear()
    for index in sorted(set(map(int, indices))):
        collection.add().index = index


def _prepare_mapping(source, target, source_indices, target_indices):
    source_vertices, source_triangles = _mesh_triangles(source)
    target_vertices, target_triangles = _mesh_triangles(target)
    incident = {}
    for triangle in target_triangles:
        for index in triangle:
            incident.setdefault(index, triangle)
    if any(index not in incident for index in target_indices):
        raise AttachmentError("Selected target vertices must belong to a surface face")
    source_edges = tuple(tuple(map(int, edge.vertices)) for edge in source.data.edges)
    target_edges = tuple(tuple(map(int, edge.vertices)) for edge in target.data.edges)
    mapping = topology_vertex_mapping(
        source_vertices, source_indices, source_edges,
        target_vertices, target_indices, target_edges)
    prepared = []
    for source_index, target_index in mapping:
        triangle = incident[target_index]
        weights = tuple(1.0 if index == target_index else 0.0
                        for index in triangle)
        prepared.append((source_index, triangle, weights,
                         source_vertices[source_index], target_vertices[target_index]))
    return source_vertices, source_triangles, target_vertices, target_triangles, prepared


def _commit_attachment(scene, source, target, source_indices, target_indices):
    export_identity.ensure_unique_persistent_ids(scene.objects)
    source_vertices, source_triangles, target_vertices, target_triangles, prepared = (
        _prepare_mapping(source, target, source_indices, target_indices))
    item = scene.cloth_next_object_attachments.add()
    item.identifier = uuid.uuid4().hex
    item.name = f"{source.name} → {target.name}"
    item.source_name, item.target_name = source.name, target.name
    item.source_role, item.target_role = source.cloth_next.role, target.cloth_next.role
    item.source_persistent_id = source.cloth_next.persistent_export_id
    item.target_persistent_id = target.cloth_next.persistent_export_id
    item.target_object = target
    item.source_topology = topology_fingerprint(len(source_vertices), source_triangles)
    item.target_topology = topology_fingerprint(len(target_vertices), target_triangles)
    _fill_indices(item.source_vertices, source_indices)
    _fill_indices(item.target_vertices, target_indices)
    for source_index, triangle, weights, source_point, target_point in prepared:
        point = item.points.add()
        point.source_index, point.target_triangle = source_index, triangle
        point.target_weights = weights
        point.source_point, point.target_point = source_point, target_point
    item.status_message = "Ready"
    scene.cloth_next_object_attachment_index = len(scene.cloth_next_object_attachments) - 1
    validation_state.mark_all_settings_dirty()
    _ensure_draw_handler()
    return item


def _group_indices(obj, name, label):
    group = obj.vertex_groups.get(name) if name else None
    if group is None:
        raise AttachmentError(f"Choose {label} on {obj.name}")
    indices = tuple(v.index for v in obj.data.vertices
                    if any(g.group == group.index and g.weight > 0
                           for g in v.groups))
    if not indices:
        raise AttachmentError(f"{label} has no vertices with positive weight")
    return indices


def _rebuild_groups(item, scene):
    source = _objects_by_identity(scene).get(str(item.source_persistent_id))
    target = item.target_object
    if not _eligible_object(None, source):
        raise AttachmentError("Source must be an enabled Cloth or Soft Body")
    if not _eligible_object(None, target):
        raise AttachmentError("Choose a Cloth or Soft Body target object")
    if source is target:
        raise AttachmentError("Choose a different target object")
    for obj in (source, target):
        if obj.mode == "EDIT":
            obj.update_from_editmode()
    source_indices = _group_indices(source, item.source_group, "Vertex Group 1")
    target_indices = _group_indices(target, item.target_group, "Vertex Group 2")
    source_vertices, source_triangles = _mesh_triangles(source)
    target_vertices, target_triangles = _mesh_triangles(target)
    # Store a real target triangle with one-hot weights for each chosen vertex.
    # This keeps the existing solver wire format and topology validation intact.
    incident = {}
    for triangle in target_triangles:
        for index in triangle:
            incident.setdefault(index, triangle)
    if any(index not in incident for index in target_indices):
        raise AttachmentError("Vertex Group 2 contains vertices without surface faces")
    search = VertexSearch(target_vertices, target_indices)
    prepared = []
    for index in source_indices:
        point = source_vertices[index]
        nearest = search.nearest(point)
        triangle = incident[nearest]
        weights = tuple(1.0 if i == nearest else 0.0 for i in triangle)
        prepared.append((index, triangle, weights, point, target_vertices[nearest]))
    item.source_name, item.target_name = source.name, target.name
    item.name = f"{source.name} → {target.name}"
    item.source_role, item.target_role = source.cloth_next.role, target.cloth_next.role
    item.target_persistent_id = target.cloth_next.persistent_export_id
    item.source_topology = topology_fingerprint(len(source_vertices), source_triangles)
    item.target_topology = topology_fingerprint(len(target_vertices), target_triangles)
    item.group_signature = _group_signature(source_indices, target_indices)
    _fill_indices(item.source_vertices, source_indices)
    _fill_indices(item.target_vertices, target_indices)
    item.points.clear()
    for index, triangle, weights, source_point, target_point in prepared:
        point = item.points.add()
        point.source_index, point.target_triangle = index, triangle
        point.target_weights = weights
        point.source_point, point.target_point = source_point, target_point
    item.needs_rebuild = False
    item.status_message = "Ready"
    _ensure_draw_handler()


def _group_signature(source_indices, target_indices):
    return hashlib.sha256(repr((source_indices, target_indices)).encode()).hexdigest()


def _validate_groups(item, scene):
    source = _objects_by_identity(scene).get(str(item.source_persistent_id))
    target = item.target_object
    if not _eligible_object(None, source) or not _eligible_object(None, target):
        raise AttachmentError("Source and target must be enabled Cloth or Soft Body objects")
    if target.cloth_next.persistent_export_id != item.target_persistent_id:
        raise AttachmentError("Target changed. Bind vertex groups again")
    for obj in (source, target):
        if obj.mode == "EDIT":
            obj.update_from_editmode()
    signature = _group_signature(
        _group_indices(source, item.source_group, "Vertex Group 1"),
        _group_indices(target, item.target_group, "Vertex Group 2"))
    if item.needs_rebuild or signature != item.group_signature or not item.points:
        raise AttachmentError("Vertex groups changed or are unbound. Bind vertex groups again")


class CLOTHNEXT_OT_bind_group_attachment(bpy.types.Operator):
    bl_idname = "clothnext.bind_group_attachment"
    bl_label = "Bind Vertex Groups"
    bl_description = "Explicitly bind the selected groups at their current positions"
    bl_options = {"REGISTER", "UNDO"}
    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        items = context.scene.cloth_next_object_attachments
        if not 0 <= self.index < len(items):
            return {"CANCELLED"}
        item = items[self.index]
        try:
            export_identity.ensure_unique_persistent_ids(context.scene.objects)
            _rebuild_groups(item, context.scene)
        except AttachmentError as exc:
            item.needs_rebuild = True
            item.status_message = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class CLOTHNEXT_OT_add_group_attachment(bpy.types.Operator):
    bl_idname = "clothnext.add_group_attachment"
    bl_label = "Add Object Attachment"
    bl_description = "Add a target object and choose a vertex group on each object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _eligible_object(None, context.object)

    def execute(self, context):
        export_identity.ensure_unique_persistent_ids(context.scene.objects)
        item = context.scene.cloth_next_object_attachments.add()
        item.identifier = uuid.uuid4().hex
        item.source_persistent_id = context.object.cloth_next.persistent_export_id
        item.source_name = context.object.name
        item.use_vertex_groups = True
        item.needs_rebuild = True
        item.status_message = "Choose a target object and both vertex groups"
        return {"FINISHED"}


class CLOTHNEXT_OT_create_object_attachment(bpy.types.Operator):
    bl_idname = "clothnext.create_object_attachment"
    bl_label = "Create Attachment From Selection"
    bl_description = ("Attach selected source vertices to their closest points "
                      "on the target surface")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return bool(obj and obj.type == "MESH" and obj.mode == "EDIT"
                    and _eligible_object(None, obj)
                    and _eligible_object(None, getattr(
                        context.scene, "cloth_next_attachment_target", None)))

    def execute(self, context):
        source = context.object
        target = context.scene.cloth_next_attachment_target
        if source is target:
            self.report({"ERROR"}, "Choose a different target object")
            return {"CANCELLED"}
        export_identity.ensure_unique_persistent_ids(context.scene.objects)
        bm = bmesh.from_edit_mesh(source.data)
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
        selected = tuple(vertex for vertex in bm.verts if vertex.select)
        if not selected:
            self.report({"ERROR"}, "Select at least one source vertex")
            return {"CANCELLED"}
        target_vertices, target_triangles = _mesh_triangles(target)
        if not target_triangles:
            self.report({"ERROR"}, "Target has no surface triangles")
            return {"CANCELLED"}
        item = context.scene.cloth_next_object_attachments.add()
        item.identifier = uuid.uuid4().hex
        item.name = f"{source.name} → {target.name}"
        item.source_name, item.target_name = source.name, target.name
        item.source_role = str(source.cloth_next.role)
        item.target_role = str(target.cloth_next.role)
        item.source_persistent_id = str(source.cloth_next.persistent_export_id)
        item.target_persistent_id = str(target.cloth_next.persistent_export_id)
        item.target_object = target
        source_vertices, source_triangles = _mesh_triangles(source)
        item.source_topology = topology_fingerprint(
            len(source_vertices), source_triangles)
        item.target_topology = topology_fingerprint(
            len(target_vertices), target_triangles)
        for vertex in selected:
            source_point = source.matrix_world @ vertex.co
            triangle, weights, closest = closest_surface_point(
                tuple(source_point), target_vertices, target_triangles)
            point = item.points.add()
            point.source_index = int(vertex.index)
            point.target_triangle = triangle
            point.target_weights = weights
            point.source_point = tuple(source_point)
            point.target_point = closest
        _fill_indices(item.source_vertices, (vertex.index for vertex in selected))
        context.scene.cloth_next_object_attachment_index = (
            len(context.scene.cloth_next_object_attachments) - 1)
        _ensure_draw_handler()
        self.report({"INFO"}, f"Created attachment with {len(selected)} points")
        return {"FINISHED"}


_editor_sessions = {}
_editor_view_handle = None
_editor_text_handle = None


def _editor_operator():
    window = getattr(bpy.context, "window", None)
    return _editor_sessions.get(window.as_pointer()) if window else None


def _editor_draw_view():  # pragma: no cover - Blender GPU callback
    operator = _editor_operator()
    if operator is None:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        for obj, color, alpha in operator._wire_specs():
            positions, edges = operator.mesh_cache[obj][0:2]
            if edges:
                batch = batch_for_shader(shader, "LINES", {"pos": positions}, indices=edges)
                shader.bind(); shader.uniform_float("color", (*color, alpha)); batch.draw(shader)
        for obj, indices, color in operator._point_specs():
            positions = operator.mesh_cache.get(obj, ((),))[0]
            points = [positions[index] for index in indices if 0 <= index < len(positions)]
            if points:
                batch = batch_for_shader(shader, "POINTS", {"pos": points})
                shader.bind(); shader.uniform_float("color", (*color, 1.0))
                gpu.state.point_size_set(8.0); batch.draw(shader)
        lines = operator.preview_lines()
        if lines:
            batch = batch_for_shader(shader, "LINES", {"pos": lines})
            shader.bind(); shader.uniform_float("color", (.35, .95, .55, .9))
            gpu.state.line_width_set(2.0); batch.draw(shader)
    finally:
        try:
            gpu.state.point_size_set(1.0); gpu.state.line_width_set(1.0)
            gpu.state.depth_test_set("NONE"); gpu.state.blend_set("NONE")
        except Exception:
            pass


def _editor_draw_text():  # pragma: no cover - Blender text callback
    operator = _editor_operator()
    if operator is None:
        return
    import blf
    labels = {
        "SOURCE_VERTEX_SELECT": ("SOURCE", "Select attachment vertices"),
        "TARGET_OBJECT_SELECT": ("TARGET", "Click a Cloth NeXt target object"),
        "TARGET_VERTEX_SELECT": ("TARGET VERTICES", "Select attachment vertices"),
    }
    title, instruction = labels.get(operator.stage, ("ATTACHMENT", ""))
    blf.position(0, 28, 72, 0); blf.size(0, 18); blf.draw(0, title)
    blf.position(0, 28, 50, 0); blf.size(0, 13); blf.draw(0, instruction)
    blf.position(0, 28, 30, 0); blf.size(0, 12)
    blf.draw(0, "LMB Select | Shift Add | B Path Select | Enter Continue | Esc Cancel")
    if operator.feedback and time.monotonic() < operator.feedback_until:
        blf.position(0, 28, 92, 0); blf.size(0, 13); blf.draw(0, operator.feedback)


def _stop_editor_handlers():
    global _editor_view_handle, _editor_text_handle
    if _editor_sessions:
        return
    for name in ("_editor_view_handle", "_editor_text_handle"):
        handle = globals()[name]
        if handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
            except Exception:
                pass
            globals()[name] = None


class CLOTHNEXT_OT_edit_attachment(bpy.types.Operator):
    """Selection-independent, two-stage artist attachment editor."""
    bl_idname = "clothnext.edit_attachment"
    bl_label = "Add Attachment"
    bl_description = "Select source and target vertices directly in the viewport"
    bl_options = {"INTERNAL", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return (getattr(context, "mode", "OBJECT") == "OBJECT"
                and _eligible_object(None, context.object)
                and not shared_controller.snapshot().active)

    def invoke(self, context, event):
        global _editor_view_handle, _editor_text_handle
        if not self.poll(context) or context.window.as_pointer() in _editor_sessions:
            return {"CANCELLED"}
        self.window, self.screen, self.workspace = context.window, context.window.screen, context.window.workspace
        self.scene, self.view_layer, self.source = context.scene, context.view_layer, context.object
        self.target = None
        self.stage = "SOURCE_VERTEX_SELECT"
        self.source_selection, self.target_selection = set(), set()
        self.hovered_vertex = None
        self.feedback, self.feedback_until = "", 0.0
        self.path_anchor = None
        self.path_select = False
        self.path_additive = False
        self.mesh_cache = {}
        self.projection_cache = {}
        self._finished = False
        self.key = self.window.as_pointer()
        _editor_sessions[self.key] = self
        try:
            self._cache_mesh(self.source)
            if _editor_view_handle is None:
                _editor_view_handle = bpy.types.SpaceView3D.draw_handler_add(
                    _editor_draw_view, (), "WINDOW", "POST_VIEW")
                _editor_text_handle = bpy.types.SpaceView3D.draw_handler_add(
                    _editor_draw_text, (), "WINDOW", "POST_PIXEL")
            context.window_manager.modal_handler_add(self)
            self.window.cursor_modal_set("CROSSHAIR")
        except Exception:
            self.finish()
            raise
        self._redraw()
        return {"RUNNING_MODAL"}

    def _cache_mesh(self, obj):
        positions = tuple(tuple(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices)
        edges = tuple(tuple(map(int, edge.vertices)) for edge in obj.data.edges)
        self.mesh_cache[obj] = (positions, edges)

    def _wire_specs(self):
        result = [(self.source, (.05, .7, .95), .25 if self.target else .65)]
        if self.target is not None:
            result.append((self.target, (.95, .42, .12), .7))
        return result

    def _point_specs(self):
        result = [(self.source, self.source_selection, (.05, .85, 1.0))]
        if self.target is not None:
            result.append((self.target, self.target_selection, (1.0, .45, .12)))
        if self.hovered_vertex is not None:
            obj = self.source if self.stage == "SOURCE_VERTEX_SELECT" else self.target
            if obj is not None:
                result.append((obj, (self.hovered_vertex,), (1.0, 1.0, .45)))
        return result

    def preview_lines(self):
        if self.target is None or not self.source_selection or not self.target_selection:
            return ()
        source_positions = self.mesh_cache[self.source][0]
        target_positions = self.mesh_cache[self.target][0]
        try:
            mapping = topology_vertex_mapping(
                source_positions, self.source_selection, self.mesh_cache[self.source][1],
                target_positions, self.target_selection, self.mesh_cache[self.target][1])
        except AttachmentError:
            return ()
        return tuple(position for source, target in mapping
                     for position in (source_positions[source], target_positions[target]))

    def _alive(self):
        try:
            return (self.window in tuple(bpy.context.window_manager.windows)
                    and self.window.screen == self.screen and self.window.workspace == self.workspace
                    and self.window.scene == self.scene and self.window.view_layer == self.view_layer
                    and self.source in tuple(self.scene.objects)
                    and _eligible_object(None, self.source)
                    and (self.target is None or self.target in tuple(self.scene.objects))
                    and not shared_controller.snapshot().active)
        except (ReferenceError, AttributeError):
            return False

    def _redraw(self):
        from .linked_colliders import redraw
        redraw(self.window)

    def _feedback(self, message):
        self.feedback, self.feedback_until = message, time.monotonic() + 1.8

    def _viewport(self, context, event):
        from .linked_colliders import viewport_at
        area, region = viewport_at(self.window, event)
        if area is None:
            return None
        return area, region, (event.mouse_x - region.x, event.mouse_y - region.y)

    def _screen_vertices(self, context, area, region, obj):
        from bpy_extras import view3d_utils
        with context.temp_override(window=self.window, area=area, region=region):
            rv3d = context.region_data
            matrix = tuple(value for row in rv3d.view_matrix for value in row)
            key = (obj, area.as_pointer(), region.as_pointer(), matrix,
                   region.width, region.height)
            projected = self.projection_cache.get(key)
            if projected is None:
                projected = tuple(view3d_utils.location_3d_to_region_2d(
                    region, rv3d, position) for position in self.mesh_cache[obj][0])
                self.projection_cache = {key: projected}
            return projected

    def _pick_vertex(self, context, event):
        viewport = self._viewport(context, event)
        obj = self.source if self.stage == "SOURCE_VERTEX_SELECT" else self.target
        if viewport is None or obj is None:
            return None
        area, region, mouse = viewport
        projected = self._screen_vertices(context, area, region, obj)
        candidates = [((point.x-mouse[0])**2 + (point.y-mouse[1])**2, index)
                      for index, point in enumerate(projected) if point is not None]
        if not candidates:
            return None
        distance, index = min(candidates)
        return index if distance <= 12.0 ** 2 else None

    def modal(self, context, event):
        if _editor_sessions.get(getattr(self, "key", None)) is not self:
            return {"CANCELLED"}
        if not self._alive() or event.type == "WINDOW_DEACTIVATE" or (
                event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS"):
            self.finish(); return {"CANCELLED"}
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"} or event.type.startswith("NUMPAD"):
            self.projection_cache.clear()
            return {"PASS_THROUGH"}
        if event.type == "B" and event.value == "PRESS" and self.stage != "TARGET_OBJECT_SELECT":
            self.path_select, self.path_anchor = True, None
            self.path_additive = bool(event.shift)
            self._feedback("Path Select: click the start and end vertices")
            return {"RUNNING_MODAL"}
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            if self.stage != "TARGET_OBJECT_SELECT":
                self.hovered_vertex = self._pick_vertex(context, event)
            self._redraw(); return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if self.stage == "TARGET_OBJECT_SELECT":
                viewport = self._viewport(context, event)
                target = None
                if viewport is not None:
                    area, region, _ = viewport
                    from .linked_colliders import object_at
                    with context.temp_override(window=self.window, area=area, region=region):
                        target = object_at(context, event, region)
                if target is self.source or not _eligible_object(None, target):
                    self._feedback("Choose another enabled Cloth or Soft Body mesh")
                else:
                    self.target = target; self._cache_mesh(target)
                    self.stage = "TARGET_VERTEX_SELECT"; self.hovered_vertex = None
                self._redraw(); return {"RUNNING_MODAL"}
            index = self._pick_vertex(context, event)
            if index is not None:
                selection = self.source_selection if self.stage == "SOURCE_VERTEX_SELECT" else self.target_selection
                if self.path_select:
                    if self.path_anchor is None:
                        self.path_anchor = index
                        self.hovered_vertex = index
                        self._feedback("Path Select: click the end vertex")
                    else:
                        obj = self.source if self.stage == "SOURCE_VERTEX_SELECT" else self.target
                        try:
                            path = shortest_mesh_path(
                                self.mesh_cache[obj][0], self.mesh_cache[obj][1],
                                self.path_anchor, index)
                        except AttachmentError as exc:
                            self._feedback(str(exc))
                        else:
                            if not self.path_additive and not event.shift:
                                selection.clear()
                            selection.update(path)
                            self._feedback(f"Selected path with {len(path)} vertices")
                        self.path_select, self.path_anchor = False, None
                else:
                    if not event.shift:
                        selection.clear()
                    if event.shift and index in selection:
                        selection.remove(index)
                    else:
                        selection.add(index)
                self._redraw()
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS":
            if self.stage == "SOURCE_VERTEX_SELECT":
                if not self.source_selection:
                    self._feedback("Select at least one source vertex")
                else:
                    self.stage = "TARGET_OBJECT_SELECT"; self.hovered_vertex = None
            elif self.stage == "TARGET_VERTEX_SELECT":
                if not self.target_selection:
                    self._feedback("Select at least one target vertex")
                else:
                    try:
                        item = _commit_attachment(self.scene, self.source, self.target,
                                                  self.source_selection, self.target_selection)
                    except AttachmentError as exc:
                        self._feedback(str(exc)); return {"RUNNING_MODAL"}
                    count = len(item.points); self.finish()
                    self.report({"INFO"}, f"Created attachment with {count} connections")
                    return {"FINISHED"}
            self._redraw(); return {"RUNNING_MODAL"}
        return {"RUNNING_MODAL"}

    def finish(self):
        if getattr(self, "_finished", False):
            return
        self._finished = True
        if _editor_sessions.get(getattr(self, "key", None)) is self:
            del _editor_sessions[self.key]
        self.mesh_cache = {}; self.projection_cache = {}; self.source = self.target = None
        try:
            self.window.cursor_modal_restore(); self._redraw()
        except (ReferenceError, AttributeError):
            pass
        _stop_editor_handlers()

    def cancel(self, context):
        self.finish()


class CLOTHNEXT_OT_remove_object_attachment(bpy.types.Operator):
    bl_idname = "clothnext.remove_object_attachment"
    bl_label = "Remove Object Attachment"
    bl_options = {"UNDO"}
    index: bpy.props.IntProperty(default=-1, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return not shared_controller.snapshot().active

    def execute(self, context):
        items = context.scene.cloth_next_object_attachments
        index = (int(self.index) if int(self.index) >= 0 else
                 int(context.scene.cloth_next_object_attachment_index))
        if 0 <= index < len(items):
            items.remove(index)
            context.scene.cloth_next_object_attachment_index = max(
                0, min(index, len(items) - 1))
            validation_state.mark_all_settings_dirty()
        return {"FINISHED"}


def snapshot_enabled(scene, deformable_entries):
    """Validate and freeze enabled relations for one authoritative Bake."""
    objects = _objects_by_identity(scene)
    entries = {str(entry.obj.cloth_next.persistent_export_id): entry
               for entry in deformable_entries}
    result = []
    for item in getattr(scene, "cloth_next_object_attachments", ()):
        if not bool(item.enabled):
            continue
        if item.use_vertex_groups:
            try:
                _validate_groups(item, scene)
            except AttachmentError as exc:
                item.needs_rebuild = True
                item.status_message = str(exc)
                raise AttachmentError(f"{item.name}: {exc}") from exc
        source = objects.get(str(item.source_persistent_id))
        target = objects.get(str(item.target_persistent_id))
        source_entry = entries.get(str(item.source_persistent_id))
        target_entry = entries.get(str(item.target_persistent_id))
        reason = ""
        if source is None or target is None:
            reason = "source or target object no longer exists"
        elif source_entry is None or target_entry is None:
            reason = "source and target must both be enabled for this Bake"
        elif source_entry.role not in SUPPORTED_ROLES or target_entry.role not in SUPPORTED_ROLES:
            reason = "only Cloth and Soft Body objects are supported"
        elif (source_entry.role != str(item.source_role)
              or target_entry.role != str(item.target_role)):
            reason = "source or target role changed"
        else:
            source_fp = topology_fingerprint(
                len(source_entry.boundary_vertices), source_entry.boundary_triangles)
            target_fp = topology_fingerprint(
                len(target_entry.boundary_vertices), target_entry.boundary_triangles)
            if source_fp != str(item.source_topology) or target_fp != str(item.target_topology):
                reason = "source or target topology changed"
        if reason:
            item.needs_rebuild = True
            item.status_message = f"Needs Rebuild: {reason}"
            raise AttachmentError(f"{item.name}: {item.status_message}")
        item.needs_rebuild = False
        item.status_message = "Ready"
        points = []
        for point in item.points:
            source_world = tuple(map(float, point.source_point))
            target_world = tuple(map(float, point.target_point))
            try:
                source_local = source.data.vertices[int(point.source_index)].co
                indices = tuple(map(int, point.target_triangle))
                weights = tuple(map(float, point.target_weights))
                target_local = (
                    target.data.vertices[indices[0]].co * weights[0]
                    + target.data.vertices[indices[1]].co * weights[1]
                    + target.data.vertices[indices[2]].co * weights[2])
                source_world = tuple(source.matrix_world @ source_local)
                target_world = tuple(target.matrix_world @ target_local)
            except (AttributeError, IndexError, TypeError):
                pass
            points.append(AttachmentPoint(
                int(point.source_index), tuple(map(int, point.target_triangle)),
                tuple(map(float, point.target_weights)), source_world,
                target_world))
        attachment = ObjectAttachment(
            str(item.identifier), str(item.name),
            export_identity.export_uuid(source), export_identity.export_uuid(target),
            str(source_entry.role), str(target_entry.role), float(item.stiffness),
            tuple(points))
        try:
            validate_attachment(
                attachment,
                source_vertex_count=len(source_entry.boundary_vertices),
                target_vertex_count=len(target_entry.boundary_vertices))
        except AttachmentError as exc:
            item.needs_rebuild = True
            item.status_message = f"Needs Rebuild: {exc}"
            raise AttachmentError(f"{item.name}: {item.status_message}") from exc
        result.append((attachment, len(source_entry.boundary_vertices),
                       len(target_entry.boundary_vertices)))
    return tuple(result)


_draw_handle = None


def _draw_overlay():  # pragma: no cover - exercised in Blender
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        scene = bpy.context.scene
        lines = []
        objects = _objects_by_identity(scene)
        for item in scene.cloth_next_object_attachments:
            if item.enabled and item.show_overlay:
                source = objects.get(str(item.source_persistent_id))
                target = objects.get(str(item.target_persistent_id))
                if source is None or target is None:
                    continue
                for point in item.points:
                    try:
                        source_local = source.data.vertices[
                            int(point.source_index)].co
                        indices = tuple(map(int, point.target_triangle))
                        weights = tuple(map(float, point.target_weights))
                        target_local = (
                            target.data.vertices[indices[0]].co * weights[0]
                            + target.data.vertices[indices[1]].co * weights[1]
                            + target.data.vertices[indices[2]].co * weights[2])
                        lines.extend((tuple(source.matrix_world @ source_local),
                                      tuple(target.matrix_world @ target_local)))
                    except (AttributeError, IndexError, TypeError):
                        continue
        if not lines:
            return
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINES", {"pos": lines})
        shader.bind()
        shader.uniform_float("color", (0.15, 0.9, 0.35, 0.85))
        batch.draw(shader)
        point_batch = batch_for_shader(shader, "POINTS", {"pos": lines})
        gpu.state.point_size_set(6.0)
        point_batch.draw(shader)
        gpu.state.point_size_set(1.0)
    except Exception:
        return


def _ensure_draw_handler():
    global _draw_handle
    if _draw_handle is None and hasattr(bpy.types, "SpaceView3D"):
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_overlay, (), "WINDOW", "POST_VIEW")


def _cancel_editor_sessions(*_args):
    for operator in tuple(_editor_sessions.values()):
        operator.finish()


_persistent = getattr(getattr(bpy.app, "handlers", None), "persistent", lambda fn: fn)
_cancel_editor_sessions = _persistent(_cancel_editor_sessions)


def register():
    _ensure_draw_handler()
    handlers = getattr(bpy.app, "handlers", None)
    if handlers is not None:
        for name in ("load_pre", "undo_pre"):
            rows = getattr(handlers, name, None)
            if rows is not None and _cancel_editor_sessions not in rows:
                rows.append(_cancel_editor_sessions)


def unregister():
    global _draw_handle
    _cancel_editor_sessions()
    handlers = getattr(bpy.app, "handlers", None)
    if handlers is not None:
        for name in ("load_pre", "undo_pre"):
            rows = getattr(handlers, name, None)
            if rows is not None and _cancel_editor_sessions in rows:
                rows.remove(_cancel_editor_sessions)
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        except Exception:
            pass
        _draw_handle = None


CLASSES = (CLOTHNEXT_PG_attachment_point,
           CLOTHNEXT_PG_attachment_vertex,
           CLOTHNEXT_PG_object_attachment,
           CLOTHNEXT_OT_add_group_attachment,
           CLOTHNEXT_OT_bind_group_attachment,
           CLOTHNEXT_OT_create_object_attachment,
           CLOTHNEXT_OT_edit_attachment,
           CLOTHNEXT_OT_remove_object_attachment)
