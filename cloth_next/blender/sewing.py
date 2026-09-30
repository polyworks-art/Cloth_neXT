# SPDX-License-Identifier: GPL-3.0-or-later
"""Marvelous-Designer-style path Sewing authoring and persistent overlay."""

from __future__ import annotations

import time
import uuid

import bpy

from .. import export_identity
from ..attachments import (AttachmentPoint, ObjectAttachment,
                           topology_fingerprint, shortest_mesh_path)
from ..bake.controller import shared_controller
from ..sewing import SewingError, interaction_visible, path_mapping
from . import validation_state


class CLOTHNEXT_PG_sewing_vertex(bpy.types.PropertyGroup):
    index: bpy.props.IntProperty(default=0, min=0)


class CLOTHNEXT_PG_sewing_pair(bpy.types.PropertyGroup):
    source_index: bpy.props.IntProperty(default=0, min=0)
    target_index: bpy.props.IntProperty(default=0, min=0)


def _settings_changed(_self, _context):
    validation_state.mark_all_settings_dirty()


class CLOTHNEXT_PG_sewing_definition(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(default="")
    name: bpy.props.StringProperty(default="Sewing")
    enabled: bpy.props.BoolProperty(default=True, update=_settings_changed)
    show_overlay: bpy.props.BoolProperty(default=True)
    ui_expanded: bpy.props.BoolProperty(default=False)
    source_persistent_id: bpy.props.StringProperty(default="")
    target_persistent_id: bpy.props.StringProperty(default="")
    source_name: bpy.props.StringProperty(default="")
    target_name: bpy.props.StringProperty(default="")
    source_topology: bpy.props.StringProperty(default="")
    target_topology: bpy.props.StringProperty(default="")
    flipped: bpy.props.BoolProperty(default=False)
    strength: bpy.props.FloatProperty(name="Strength", default=1.0, min=0.0,
                                      update=_settings_changed)
    status_message: bpy.props.StringProperty(default="Ready")
    side_a: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_sewing_vertex)
    side_b: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_sewing_vertex)
    mapping: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_sewing_pair)


def _eligible(_self, obj):
    settings = getattr(obj, "cloth_next", None)
    return bool(obj and obj.type == "MESH" and settings and settings.enabled
                and settings.role == "CLOTH")


def _objects(scene):
    return {str(obj.cloth_next.persistent_export_id): obj
            for obj in getattr(scene, "objects", ())
            if getattr(obj, "cloth_next", None)}


def _mesh(obj):
    obj.data.calc_loop_triangles()
    vertices = tuple(tuple(obj.matrix_world @ vertex.co) for vertex in obj.data.vertices)
    edges = tuple(tuple(map(int, edge.vertices)) for edge in obj.data.edges)
    triangles = tuple(tuple(map(int, tri.vertices)) for tri in obj.data.loop_triangles)
    return vertices, edges, triangles


def _boundary_edges(obj):
    counts = {}
    for polygon in obj.data.polygons:
        for edge in polygon.edge_keys:
            key = tuple(sorted(map(int, edge)))
            counts[key] = counts.get(key, 0) + 1
    return tuple(edge for edge, count in counts.items() if count == 1)


def _fill(collection, values):
    collection.clear()
    for value in values:
        collection.add().index = int(value)


def _rebuild_mapping(item, source, target):
    source_vertices, _, _ = _mesh(source)
    target_vertices, _, _ = _mesh(target)
    side_a = tuple(row.index for row in item.side_a)
    side_b = tuple(row.index for row in item.side_b)
    mapping = path_mapping(source_vertices, side_a, target_vertices, side_b,
                           flipped=bool(item.flipped))
    item.mapping.clear()
    for source_index, target_index in mapping:
        row = item.mapping.add()
        row.source_index, row.target_index = source_index, target_index


def _commit(scene, source, target, side_a, side_b, flipped=False):
    export_identity.ensure_unique_persistent_ids(scene.objects)
    source_vertices, _, source_triangles = _mesh(source)
    target_vertices, _, target_triangles = _mesh(target)
    mapping = path_mapping(source_vertices, side_a, target_vertices, side_b,
                           flipped=flipped)
    item = scene.cloth_next_sewing_definitions.add()
    item.identifier = uuid.uuid4().hex
    item.name = f"{source.name} → {target.name}"
    item.source_name, item.target_name = source.name, target.name
    item.source_persistent_id = source.cloth_next.persistent_export_id
    item.target_persistent_id = target.cloth_next.persistent_export_id
    item.source_topology = topology_fingerprint(len(source_vertices), source_triangles)
    item.target_topology = topology_fingerprint(len(target_vertices), target_triangles)
    item.flipped = bool(flipped)
    _fill(item.side_a, side_a); _fill(item.side_b, side_b)
    for source_index, target_index in mapping:
        row = item.mapping.add()
        row.source_index, row.target_index = source_index, target_index
    scene.cloth_next_sewing_index = len(scene.cloth_next_sewing_definitions) - 1
    validation_state.mark_all_settings_dirty()
    _ensure_draw_handler()
    return item


def snapshot_enabled(scene, deformable_entries):
    """Return explicit intra pairs and solver-compatible cross attachments."""
    objects = _objects(scene)
    entries = {str(row.obj.cloth_next.persistent_export_id): row
               for row in deformable_entries}
    intra, cross = {}, []
    for item in getattr(scene, "cloth_next_sewing_definitions", ()):
        if not item.enabled:
            continue
        source, target = (objects.get(str(item.source_persistent_id)),
                          objects.get(str(item.target_persistent_id)))
        source_entry, target_entry = (entries.get(str(item.source_persistent_id)),
                                      entries.get(str(item.target_persistent_id)))
        reason = ""
        if source is None or target is None:
            reason = "source or target object no longer exists"
        elif source_entry is None or target_entry is None:
            reason = "both sewn objects must be enabled for this Bake"
        elif source_entry.role != "CLOTH" or target_entry.role != "CLOTH":
            reason = "Sewing supports Cloth objects"
        else:
            sf = topology_fingerprint(len(source_entry.boundary_vertices),
                                      source_entry.boundary_triangles)
            tf = topology_fingerprint(len(target_entry.boundary_vertices),
                                      target_entry.boundary_triangles)
            if sf != item.source_topology or tf != item.target_topology:
                reason = "source or target topology changed"
        if reason:
            item.status_message = f"Needs Rebuild: {reason}"
            raise SewingError(f"{item.name}: {item.status_message}")
        pairs = tuple((int(row.source_index), int(row.target_index))
                      for row in item.mapping)
        if source is target:
            intra.setdefault(str(item.source_persistent_id), []).extend(pairs)
        else:
            target.data.calc_loop_triangles()
            incident = {}
            for tri in target.data.loop_triangles:
                triangle = tuple(map(int, tri.vertices))
                for index in triangle:
                    incident.setdefault(index, triangle)
            points = []
            for source_index, target_index in pairs:
                triangle = incident.get(target_index)
                if triangle is None:
                    raise SewingError(f"{item.name}: target path is not on a surface")
                weights = tuple(1.0 if i == target_index else 0.0 for i in triangle)
                points.append(AttachmentPoint(
                    source_index, triangle, weights,
                    tuple(source.matrix_world @ source.data.vertices[source_index].co),
                    tuple(target.matrix_world @ target.data.vertices[target_index].co)))
            attachment = ObjectAttachment(
                item.identifier, item.name, export_identity.export_uuid(source),
                export_identity.export_uuid(target), "CLOTH", "CLOTH",
                float(item.strength), tuple(points))
            cross.append((attachment, len(source_entry.boundary_vertices),
                          len(target_entry.boundary_vertices)))
        item.status_message = "Ready"
    return ({key: tuple(value) for key, value in intra.items()}, tuple(cross))


class CLOTHNEXT_OT_remove_sewing(bpy.types.Operator):
    bl_idname = "clothnext.remove_sewing"
    bl_label = "Remove Sewing"
    bl_options = {"UNDO"}
    index: bpy.props.IntProperty(default=-1, options={"HIDDEN"})

    def execute(self, context):
        rows = context.scene.cloth_next_sewing_definitions
        index = self.index if self.index >= 0 else context.scene.cloth_next_sewing_index
        if 0 <= index < len(rows):
            rows.remove(index)
            context.scene.cloth_next_sewing_index = max(0, min(index, len(rows)-1))
            validation_state.mark_all_settings_dirty()
        return {"FINISHED"}


class CLOTHNEXT_OT_flip_sewing(bpy.types.Operator):
    bl_idname = "clothnext.flip_sewing"
    bl_label = "Flip Sewing Direction"
    bl_options = {"UNDO"}
    index: bpy.props.IntProperty(default=-1, options={"HIDDEN"})

    def execute(self, context):
        rows = context.scene.cloth_next_sewing_definitions
        index = self.index if self.index >= 0 else context.scene.cloth_next_sewing_index
        if not (0 <= index < len(rows)): return {"CANCELLED"}
        item, objects = rows[index], _objects(context.scene)
        source, target = objects.get(item.source_persistent_id), objects.get(item.target_persistent_id)
        if source is None or target is None: return {"CANCELLED"}
        item.flipped = not item.flipped
        _rebuild_mapping(item, source, target)
        validation_state.mark_all_settings_dirty()
        return {"FINISHED"}


_editor_sessions = {}
_editor_view_handle = None
_editor_text_handle = None


class CLOTHNEXT_OT_edit_sewing(bpy.types.Operator):
    bl_idname = "clothnext.edit_sewing"
    bl_label = "Add Sewing"
    bl_description = "Pick two endpoints for each seam side"
    bl_options = {"INTERNAL", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and _eligible(None, context.object) and not shared_controller.snapshot().active

    def invoke(self, context, _event):
        global _editor_view_handle, _editor_text_handle
        self.window, self.scene, self.source = context.window, context.scene, context.object
        self.target = None; self.stage = "A_START"; self.a = []; self.b = []
        self.hover = None; self.feedback = "Click Side A start vertex"
        self.cache = {}; self.key = self.window.as_pointer(); self.finished = False
        _editor_sessions[self.key] = self
        self._cache(self.source)
        if _editor_view_handle is None:
            _editor_view_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_editor, (), "WINDOW", "POST_VIEW")
            _editor_text_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_editor_text, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self); self.window.cursor_modal_set("CROSSHAIR")
        return {"RUNNING_MODAL"}

    def _cache(self, obj):
        vertices, edges, _ = _mesh(obj)
        self.cache[obj] = (vertices, edges, _boundary_edges(obj))

    def _viewport(self, event):
        from .linked_colliders import viewport_at
        area, region = viewport_at(self.window, event)
        return None if area is None else (area, region, (event.mouse_x-region.x, event.mouse_y-region.y))

    def _pick(self, context, event, obj):
        viewport = self._viewport(event)
        if viewport is None: return None
        area, region, mouse = viewport
        from bpy_extras import view3d_utils
        with context.temp_override(window=self.window, area=area, region=region):
            points = tuple(view3d_utils.location_3d_to_region_2d(region, context.region_data, p)
                           for p in self.cache[obj][0])
        choices = [((p.x-mouse[0])**2+(p.y-mouse[1])**2, i)
                   for i, p in enumerate(points) if p is not None]
        if not choices: return None
        distance, index = min(choices)
        return index if distance <= 144 else None

    def _path(self, obj, start, end):
        vertices, edges, boundary = self.cache[obj]
        return shortest_mesh_path(vertices, edges, start, end,
                                  boundary_edges=boundary, interior_penalty=8.0)

    def modal(self, context, event):
        if event.type in {"ESC", "RIGHTMOUSE", "WINDOW_DEACTIVATE"} and event.value == "PRESS":
            self.finish(); return {"CANCELLED"}
        if shared_controller.snapshot().active:
            self.finish(); return {"CANCELLED"}
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"} or event.type.startswith("NUMPAD"):
            return {"PASS_THROUGH"}
        obj = self.source if self.stage.startswith("A") else self.target
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"} and obj:
            self.hover = self._pick(context, event, obj); return {"RUNNING_MODAL"}
        if event.type == "F" and event.value == "PRESS" and self.a and self.b:
            self.b.reverse(); self.feedback = "Direction flipped"; return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            if self.stage == "TARGET":
                viewport = self._viewport(event)
                if viewport:
                    area, region, _ = viewport
                    from .linked_colliders import object_at
                    with context.temp_override(window=self.window, area=area, region=region):
                        target = object_at(context, event, region)
                    if _eligible(None, target):
                        self.target = target; self._cache(target); self.stage = "B_START"
                        self.feedback = "Click Side B start vertex"
                return {"RUNNING_MODAL"}
            index = self._pick(context, event, obj) if obj else None
            if index is None: return {"RUNNING_MODAL"}
            if self.stage == "A_START": self.a=[index]; self.stage="A_END"; self.feedback="Click Side A end vertex"
            elif self.stage == "A_END":
                if index == self.a[0]: return {"RUNNING_MODAL"}
                self.a=list(self._path(self.source,self.a[0],index)); self.stage="TARGET"; self.feedback="Click Side B object (may be the same object)"
            elif self.stage == "B_START": self.b=[index]; self.stage="B_END"; self.feedback="Click Side B end vertex"
            elif self.stage == "B_END":
                if index == self.b[0]: return {"RUNNING_MODAL"}
                self.b=list(self._path(self.target,self.b[0],index)); self.stage="PREVIEW"; self.feedback="Enter Commit | F Flip | Esc Cancel"
            return {"RUNNING_MODAL"}
        if event.type in {"RET", "NUMPAD_ENTER"} and event.value == "PRESS" and self.stage == "PREVIEW":
            try: item = _commit(self.scene, self.source, self.target, self.a, self.b)
            except SewingError as exc: self.feedback=str(exc); return {"RUNNING_MODAL"}
            self.finish(); self.report({"INFO"}, f"Created {item.name}"); return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def finish(self):
        global _editor_view_handle, _editor_text_handle
        if self.finished: return
        self.finished=True; _editor_sessions.pop(self.key, None)
        try: self.window.cursor_modal_restore()
        except Exception: pass
        if not _editor_sessions:
            for handle in (_editor_view_handle, _editor_text_handle):
                if handle:
                    try: bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
                    except Exception: pass
            _editor_view_handle = _editor_text_handle = None


def _draw_editor():
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        op = next(iter(_editor_sessions.values()), None)
        if op is None: return
        lines=[]
        for obj, path in ((op.source,op.a),(op.target,op.b)):
            if obj and len(path)>1:
                positions=op.cache[obj][0]
                for a,b in zip(path,path[1:]): lines.extend((positions[a],positions[b]))
        if op.stage == "PREVIEW":
            sv,tv=op.cache[op.source][0],op.cache[op.target][0]
            for a,b in path_mapping(sv,op.a,tv,op.b): lines.extend((sv[a],tv[b]))
        if lines:
            shader=gpu.shader.from_builtin("UNIFORM_COLOR"); batch=batch_for_shader(shader,"LINES",{"pos":lines})
            shader.bind(); shader.uniform_float("color",(.1,.8,1,.9)); batch.draw(shader)
    except Exception: pass


def _draw_editor_text():
    try:
        import blf
        op=next(iter(_editor_sessions.values()),None)
        if op: blf.position(0,28,50,0); blf.size(0,14); blf.draw(0,"SEWING — "+op.feedback)
    except Exception: pass


_draw_handle = None


def _playback(scene):
    return bool(getattr(getattr(bpy.context, "screen", None), "is_animation_playing", False))


def _draw_overlay():
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        scene=bpy.context.scene
        if not interaction_visible(getattr(scene,"cloth_next_show_sewing",False), True,
                                   playback=_playback(scene), baking=shared_controller.snapshot().active): return
        objects=_objects(scene); lines=[]
        for item in scene.cloth_next_sewing_definitions:
            if not item.enabled or not item.show_overlay: continue
            source,target=objects.get(item.source_persistent_id),objects.get(item.target_persistent_id)
            if source is None or target is None: continue
            sv,_,_=_mesh(source); tv,_,_=_mesh(target)
            for rows,positions in ((item.side_a,sv),(item.side_b,tv)):
                path=[row.index for row in rows]
                for a,b in zip(path,path[1:]): lines.extend((positions[a],positions[b]))
            for pair in item.mapping: lines.extend((sv[pair.source_index],tv[pair.target_index]))
        if lines:
            shader=gpu.shader.from_builtin("UNIFORM_COLOR"); batch=batch_for_shader(shader,"LINES",{"pos":lines})
            shader.bind(); shader.uniform_float("color",(.1,.9,1,.8)); batch.draw(shader)
    except Exception: pass


def _ensure_draw_handler():
    global _draw_handle
    if _draw_handle is None and hasattr(bpy.types,"SpaceView3D"):
        _draw_handle=bpy.types.SpaceView3D.draw_handler_add(_draw_overlay,(),"WINDOW","POST_VIEW")


def register(): _ensure_draw_handler()


def unregister():
    global _draw_handle
    for op in tuple(_editor_sessions.values()): op.finish()
    if _draw_handle:
        try: bpy.types.SpaceView3D.draw_handler_remove(_draw_handle,"WINDOW")
        except Exception: pass
        _draw_handle=None


CLASSES=(CLOTHNEXT_PG_sewing_vertex, CLOTHNEXT_PG_sewing_pair,
         CLOTHNEXT_PG_sewing_definition, CLOTHNEXT_OT_remove_sewing,
         CLOTHNEXT_OT_flip_sewing, CLOTHNEXT_OT_edit_sewing)
