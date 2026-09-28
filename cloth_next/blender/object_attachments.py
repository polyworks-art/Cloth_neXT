# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent Cloth/Soft-Body object attachments and viewport preview."""

from __future__ import annotations

import uuid

import bpy

from .. import export_identity
from ..attachments import (AttachmentError, AttachmentPoint, DEFAULT_STIFFNESS,
                           ObjectAttachment, SUPPORTED_ROLES,
                           closest_surface_point, topology_fingerprint,
                           validate_attachment)

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


class CLOTHNEXT_PG_object_attachment(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(default="")
    name: bpy.props.StringProperty(default="Object Attachment")
    enabled: bpy.props.BoolProperty(default=True)
    stiffness: bpy.props.FloatProperty(
        name="Stiffness", default=DEFAULT_STIFFNESS, min=0.0)
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


def _eligible_object(_self, obj):
    settings = getattr(obj, "cloth_next", None)
    return bool(obj and getattr(obj, "type", "") == "MESH" and settings
                and settings.enabled and settings.role in SUPPORTED_ROLES)


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
        context.scene.cloth_next_object_attachment_index = (
            len(context.scene.cloth_next_object_attachments) - 1)
        _ensure_draw_handler()
        self.report({"INFO"}, f"Created attachment with {len(selected)} points")
        return {"FINISHED"}


class CLOTHNEXT_OT_remove_object_attachment(bpy.types.Operator):
    bl_idname = "clothnext.remove_object_attachment"
    bl_label = "Remove Object Attachment"
    bl_options = {"UNDO"}
    index: bpy.props.IntProperty(default=-1, options={"HIDDEN"})

    def execute(self, context):
        items = context.scene.cloth_next_object_attachments
        index = (int(self.index) if int(self.index) >= 0 else
                 int(context.scene.cloth_next_object_attachment_index))
        if 0 <= index < len(items):
            items.remove(index)
            context.scene.cloth_next_object_attachment_index = max(
                0, min(index, len(items) - 1))
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


def register():
    _ensure_draw_handler()


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, "WINDOW")
        except Exception:
            pass
        _draw_handle = None


CLASSES = (CLOTHNEXT_PG_attachment_point,
           CLOTHNEXT_PG_object_attachment,
           CLOTHNEXT_OT_create_object_attachment,
           CLOTHNEXT_OT_remove_object_attachment)
