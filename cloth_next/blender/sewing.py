# SPDX-License-Identifier: GPL-3.0-or-later
"""Marvelous-Designer-style path Sewing authoring and persistent overlay."""

from __future__ import annotations

import logging
import uuid

import bpy

from .. import export_identity
from ..attachments import (AttachmentError, AttachmentPoint, ObjectAttachment,
                           topology_fingerprint)
from ..bake.controller import shared_controller
from ..sewing import (SewingError, interaction_visible, path_mapping,
                      sampled_path_mapping, sampled_target_point, solver_stitch_stiffness)
from . import validation_state
from ..sewing_visual import segment, validate_paths, length
from .sewing_overlay import renderer

_log = logging.getLogger(__name__)


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
    flipped: bpy.props.BoolProperty(default=False, update=_settings_changed)
    strength: bpy.props.FloatProperty(
        name="Strength", default=100.0, min=0.0, soft_max=100.0,
        description="Seam pull strength (100 is a firm production seam)",
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
    source_vertices, source_edges, source_triangles = _mesh(source)
    target_vertices, target_edges, target_triangles = _mesh(target)
    try:
        validate_paths(source_vertices, source_edges, side_a, target_vertices, target_edges, side_b, source is target)
    except ValueError as exc:
        raise SewingError(str(exc)) from exc
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
    """Return dynamic stitch entries for intra- and cross-object Sewing."""
    objects = _objects(scene)
    entries = {str(row.obj.cloth_next.persistent_export_id): row
               for row in deformable_entries}
    stitches = []
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
        stiffness = solver_stitch_stiffness(item.strength)
        if stiffness == 0.0:
            item.status_message = "Inactive (Strength 0)"
            continue
        # Use Gaia's explicit six-slot stitch representation for both intra-
        # and cross-object definitions. The old intra path silently discarded
        # the definition's Strength and reused the object's legacy loose-edge
        # stiffness. Both endpoints below remain live simulated vertices;
        # source_points/target_points are diagnostics, not world-space pins.
        target.data.calc_loop_triangles()
        incident = {}
        for tri in target.data.loop_triangles:
            triangle = tuple(map(int, tri.vertices))
            for a, b in zip(triangle, triangle[1:] + triangle[:1]):
                incident.setdefault(tuple(sorted((a, b))), triangle)
        source_vertices, _, _ = _mesh(source)
        target_vertices, _, _ = _mesh(target)
        samples = sampled_path_mapping(
            source_vertices, tuple(row.index for row in item.side_a),
            target_vertices, tuple(row.index for row in item.side_b),
            flipped=bool(item.flipped))
        points = []
        for source_index, a, b, fraction in samples:
            if source is target and ((source_index == a and fraction == 0.0)
                                     or (source_index == b and fraction == 1.0)):
                continue
            triangle = incident.get(tuple(sorted((a, b))))
            if triangle is None:
                raise SewingError(f"{item.name}: target path is not on a surface")
            weights = tuple((1.0 - fraction if i == a else
                             fraction if i == b else 0.0) for i in triangle)
            points.append(AttachmentPoint(
                source_index, triangle, weights,
                source_vertices[source_index],
                sampled_target_point(target_vertices, a, b, fraction)))
        if not points:
            raise SewingError(f"{item.name}: the seam has no distinct vertex pairs")
        attachment = ObjectAttachment(
            item.identifier, item.name, export_identity.export_uuid(source),
            export_identity.export_uuid(target), "CLOTH", "CLOTH",
            stiffness, tuple(points),
            allow_same_object=True)
        stitches.append((attachment, len(source_entry.boundary_vertices),
                         len(target_entry.boundary_vertices)))
        # Also constrain target vertices that lie between source samples.
        # The six-slot protocol has a vertex on its source side, so these
        # symmetric rows are an explicit reverse attachment, not world pins.
        covered = {a if fraction <= 1e-10 else b for _, a, b, fraction in samples
                   if fraction <= 1e-10 or fraction >= 1-1e-10}
        reverse_samples = tuple(row for row in sampled_path_mapping(
            target_vertices, tuple(row.index for row in item.side_b),
            source_vertices, tuple(row.index for row in item.side_a),
            flipped=bool(item.flipped)) if row[0] not in covered)
        if reverse_samples:
            source.data.calc_loop_triangles()
            source_incident = {}
            for tri in source.data.loop_triangles:
                triangle = tuple(map(int, tri.vertices))
                for a, b in zip(triangle, triangle[1:] + triangle[:1]):
                    source_incident.setdefault(tuple(sorted((a,b))), triangle)
            reverse_points = []
            for source_index, a, b, fraction in reverse_samples:
                triangle = source_incident.get(tuple(sorted((a,b))))
                if triangle is None:
                    raise SewingError(f"{item.name}: source path is not on a surface")
                weights = tuple(1-fraction if i == a else fraction if i == b else 0 for i in triangle)
                reverse_points.append(AttachmentPoint(source_index, triangle, weights,
                    target_vertices[source_index], sampled_target_point(source_vertices, a, b, fraction)))
            reverse = ObjectAttachment(item.identifier + "_reverse", item.name,
                export_identity.export_uuid(target), export_identity.export_uuid(source),
                "CLOTH", "CLOTH", stiffness, tuple(reverse_points), allow_same_object=True)
            stitches.append((reverse, len(target_vertices), len(source_vertices)))
        item.status_message = "Ready"
    return ({}, tuple(stitches))


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


class CLOTHNEXT_PG_sewing_visual(bpy.types.PropertyGroup):
    lines: bpy.props.BoolProperty(name="Show Sewing Lines", default=True)
    threads: bpy.props.BoolProperty(name="Show Threads", default=True)
    notches: bpy.props.BoolProperty(name="Show Directional Notches", default=True)
    lengths: bpy.props.BoolProperty(name="Show Length Difference", default=True)
    inactive: bpy.props.BoolProperty(name="Show Inactive Seams", default=True)
    density: bpy.props.FloatProperty(name="Thread Density", default=1, min=.1, max=4)
    opacity: bpy.props.FloatProperty(name="Thread Opacity", default=.55, min=0, max=1, subtype="FACTOR")
    threshold: bpy.props.FloatProperty(name="Relative Warning", default=.1, min=0, max=1, subtype="FACTOR")
    absolute: bpy.props.FloatProperty(name="Absolute Warning", default=.01, min=0, subtype="DISTANCE", unit="LENGTH")
    xray: bpy.props.BoolProperty(name="See Through", default=False)


_editor_sessions = {}
_editor_view_handle = None
_editor_text_handle = None
_geometry = {}


@validation_state.persistent
def _invalidate(*_args):
    if len(_args) > 1 and hasattr(_args[1], 'updates'):
        updates = tuple(_args[1].updates)
        if not any(getattr(update, 'is_updated_geometry', False)
                   or getattr(update, 'is_updated_transform', False) for update in updates):
            return
    _geometry.clear()
    renderer.clear()
    for op in tuple(_editor_sessions.values()):
        op.cache.clear()


@validation_state.persistent
def _reset_editors(*_args):
    for op in tuple(_editor_sessions.values()):
        op.finish()
    _invalidate()


def _cached_mesh(obj):
    key = obj.as_pointer()
    if key not in _geometry:
        _geometry[key] = _mesh(obj)
    return _geometry[key]


class CLOTHNEXT_OT_edit_sewing(bpy.types.Operator):
    bl_idname = "clothnext.edit_sewing"
    bl_label = "Sewing"
    bl_description = "Select edge segments directly, or trace connected edges in Free mode"
    bl_options = {"INTERNAL", "BLOCKING"}
    mode: bpy.props.EnumProperty(items=(("SEGMENT", "Segment", ""), ("FREE", "Free", ""), ("EDIT", "Edit", ""), ("MULTI", "Multi", "Ordered path groups; Enter separates groups")), default="SEGMENT")
    reassign: bpy.props.EnumProperty(items=(("NONE", "None", ""), ("A", "Side A", ""), ("B", "Side B", "")), default="NONE")

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and _eligible(None, context.object) and not shared_controller.snapshot().active

    def invoke(self, context, event):
        global _editor_view_handle, _editor_text_handle
        self.window, self.scene = context.window, context.scene
        self.key = self.window.as_pointer()
        if self.key in _editor_sessions:
            self.report({'WARNING'}, "Sewing is already active in this window")
            return {'CANCELLED'}
        self.finished = False
        self.cache = {}
        bpy.ops.ed.undo_push(message="Begin Sewing")
        self.mouse = (event.mouse_x, event.mouse_y)
        self.reset()
        _editor_sessions[self.key] = self
        if _editor_view_handle is None:
            _editor_view_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_editor, (), "WINDOW", "POST_VIEW")
        context.window_manager.modal_handler_add(self)
        self.window.cursor_modal_set("CROSSHAIR")
        self._redraw()
        return {"RUNNING_MODAL"}

    def reset(self):
        self.source = self.target = None
        self.a, self.b = [], []
        self.hover = None
        self.flipped = False
        self.invalid = False
        self.stage = "IDLE"
        self.group_a, self.group_b = [], []
        self.group_stage = "A"
        self.feedback = "Click Side A | Enter finishes Free side | F reverse | Esc exit"

    def _cache(self, obj):
        if obj not in self.cache:
            vertices, edges, _ = _cached_mesh(obj)
            self.cache[obj] = (vertices, edges, _boundary_edges(obj))
        return self.cache[obj]

    def _viewport(self, event):
        from .linked_colliders import viewport_at
        area, region = viewport_at(self.window, event)
        return None if area is None else (area, region, (event.mouse_x-region.x, event.mouse_y-region.y))

    def _pick(self, context, event):
        viewport = self._viewport(event)
        if viewport is None:
            return None
        area, region, mouse = viewport
        from bpy_extras import view3d_utils
        from mathutils import Vector
        best = None
        with context.temp_override(window=self.window, area=area, region=region):
            rv3d = context.region_data
            for obj in self.scene.objects:
                if not _eligible(None, obj) or not obj.visible_get(view_layer=context.view_layer):
                    continue
                vertices, edges, boundary = self._cache(obj)
                candidates = boundary if self.mode in {"SEGMENT", "MULTI"} else edges
                for a, b in candidates:
                    pa, pb = (view3d_utils.location_3d_to_region_2d(region, rv3d, Vector(vertices[i])) for i in (a, b))
                    if pa is None or pb is None:
                        continue
                    delta = pb-pa
                    t = max(0, min(1, (Vector(mouse)-pa).dot(delta) / max(delta.length_squared, 1e-12)))
                    distance = (pa + t*delta-Vector(mouse)).length_squared
                    if distance > 144 or (best and distance >= best[0]):
                        continue
                    point = Vector(vertices[a]).lerp(Vector(vertices[b]), t)
                    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
                    ray = point-origin
                    if ray.length > 1e-8:
                        hit, location, *_ = self.scene.ray_cast(context.evaluated_depsgraph_get(), origin, ray.normalized(), distance=ray.length)
                        if hit and (location-origin).length < ray.length - max(1e-4, ray.length*.0001):
                            continue
                    path = segment(vertices, boundary, (a, b)) if self.mode in {"SEGMENT", "MULTI"} else (a, b)
                    best = (distance, obj, path, a if t < .5 else b)
        return best[1:] if best else None

    def preview_paths(self):
        a, b = tuple(self.a), tuple(self.b)
        if self.hover and self.stage != "PREVIEW":
            obj, path, vertex = self.hover
            if self.mode == "SEGMENT":
                if self.stage == "IDLE":
                    a = path
                elif self.stage == "SELECT_SIDE_B":
                    b = path
            elif self.mode == 'FREE':
                chosen = a if self.stage in {'IDLE', 'SELECT_SIDE_A'} else b
                owner = self.source if self.stage in {'IDLE', 'SELECT_SIDE_A'} else self.target
                if owner is obj and chosen and vertex not in chosen and tuple(sorted((chosen[-1], vertex))) in {tuple(sorted(e)) for e in self._cache(obj)[1]}:
                    if self.stage in {'IDLE', 'SELECT_SIDE_A'}:
                        a = chosen + (vertex,)
                    else:
                        b = chosen + (vertex,)
        return a, b

    def _redraw(self):
        from .linked_colliders import redraw
        redraw(self.window)

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except (ReferenceError, RuntimeError, ValueError, SewingError) as exc:
            self.feedback = str(exc)
            self.invalid = True
            self._redraw()
            _log.warning("Sewing interaction: %s", exc)
            return {'RUNNING_MODAL'}

    def _modal(self, context, event):
        if self.finished:
            return {"FINISHED"}
        if context.scene != self.scene:
            self.finish()
            return {"CANCELLED"}
        self.mouse = (event.mouse_x, event.mouse_y)
        if shared_controller.snapshot().active or context.screen.is_animation_playing or context.mode != 'OBJECT':
            self.finish()
            return {'CANCELLED'}
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            if self.stage == 'IDLE':
                self.finish()
                return {'FINISHED'}
            self.reset()
            self._redraw()
            return {'RUNNING_MODAL'}
        if event.type in {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE"} or event.type.startswith("NUMPAD") and event.type != 'NUMPAD_ENTER' or event.alt:
            return {'PASS_THROUGH'}
        if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self.hover = self._pick(context, event)
            self.invalid = False
            self._redraw()
        if event.type == 'F' and event.value == 'PRESS':
            if self.mode == 'EDIT':
                bpy.ops.clothnext.flip_sewing(index=self.scene.cloth_next_sewing_index)
            else:
                self.flipped = not self.flipped
            self._redraw()
        if event.type == 'BACK_SPACE' and event.value == 'PRESS':
            if self.mode == 'MULTI':
                group = self.group_a if self.group_stage == 'A' else self.group_b
                if group:
                    group.pop()
                self._redraw()
                return {'RUNNING_MODAL'}
            path = self.b if self.stage in {'SELECT_SIDE_B', 'PREVIEW'} else self.a
            if path:
                path.pop()
            self.stage = 'SELECT_SIDE_B' if self.source and len(self.a) > 1 else 'SELECT_SIDE_A' if self.a else 'IDLE'
            self._redraw()
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.hover = self._pick(context, event)
            if not self.hover:
                return {'RUNNING_MODAL'}
            obj, path, vertex = self.hover
            if self.mode == 'MULTI' and self.stage != 'PREVIEW':
                group = self.group_a if self.group_stage == 'A' else self.group_b
                if (obj, path) not in group:
                    group.append((obj, path))
                self.stage = 'SELECT_SIDE_A' if self.group_stage == 'A' else 'SELECT_SIDE_B'
                self.feedback = 'Multi: click paths in order | Enter next group/preview | Backspace last path'
            elif self.mode == 'EDIT' and self.reassign != 'NONE':
                rows = self.scene.cloth_next_sewing_definitions
                if self.scene.cloth_next_sewing_index < len(rows):
                    item = rows[self.scene.cloth_next_sewing_index]
                    objects = _objects(self.scene)
                    source, target = objects.get(item.source_persistent_id), objects.get(item.target_persistent_id)
                    a, b = tuple(r.index for r in item.side_a), tuple(r.index for r in item.side_b)
                    # Reassign a boundary segment without replacing the definition identity.
                    vertices, edges, boundary = self._cache(obj)
                    path = segment(vertices, boundary, path)
                    source, a = (obj, path) if self.reassign == 'A' else (source, a)
                    target, b = (obj, path) if self.reassign == 'B' else (target, b)
                    sv, se, st = _mesh(source)
                    tv, te, tt = _mesh(target)
                    validate_paths(sv, se, a, tv, te, b, source is target)
                    item.source_persistent_id, item.target_persistent_id = source.cloth_next.persistent_export_id, target.cloth_next.persistent_export_id
                    item.source_name, item.target_name = source.name, target.name
                    item.source_topology, item.target_topology = topology_fingerprint(len(sv), st), topology_fingerprint(len(tv), tt)
                    _fill(item.side_a, a)
                    _fill(item.side_b, b)
                    _rebuild_mapping(item, source, target)
                    validation_state.mark_all_settings_dirty()
                    bpy.ops.ed.undo_push(message='Reassign Sewing Side')
                    self.reassign = 'NONE'
                    self.feedback = 'Side reassigned | F reverse | X delete'
            elif self.mode == 'EDIT':
                for i, item in enumerate(self.scene.cloth_next_sewing_definitions):
                    identifier = obj.cloth_next.persistent_export_id
                    if ((item.source_persistent_id == identifier and vertex in [r.index for r in item.side_a]) or
                        (item.target_persistent_id == identifier and vertex in [r.index for r in item.side_b])):
                        self.scene.cloth_next_sewing_index = i
                        item.ui_expanded = True
                        self.feedback = 'Selected ' + item.name + ' | F reverse | X delete | E enable/disable | A/B reassign side'
                        break
            elif self.stage != 'PREVIEW':
                side_a = self.stage in {'IDLE', 'SELECT_SIDE_A'}
                chosen = self.a if side_a else self.b
                owner = self.source if side_a else self.target
                if owner is not None and owner is not obj:
                    raise SewingError('Finish this path before changing objects')
                if side_a:
                    self.source = obj
                else:
                    self.target = obj
                if self.mode == 'SEGMENT':
                    chosen[:] = path
                    self.stage = 'SELECT_SIDE_B' if side_a else 'PREVIEW'
                else:
                    if chosen and vertex != chosen[-1]:
                        if tuple(sorted((chosen[-1], vertex))) not in {tuple(sorted(e)) for e in self._cache(obj)[1]}:
                            raise SewingError('Choose the next connected edge; Free Sewing never reroutes')
                        if vertex in chosen:
                            raise SewingError('Path may not repeat a vertex')
                    if not chosen or vertex != chosen[-1]:
                        chosen.append(vertex)
                    self.stage = 'SELECT_SIDE_A' if side_a else 'SELECT_SIDE_B'
                self.feedback = 'Click Side B | Enter finishes side/commits | F reverse | Backspace undo point | Esc cancel'
            self._redraw()
        if self.mode == 'EDIT' and event.value == 'PRESS':
            if event.type in {'A', 'B'}:
                self.reassign = event.type
                self.feedback = 'Click replacement boundary for Side ' + event.type
            rows = self.scene.cloth_next_sewing_definitions
            index = self.scene.cloth_next_sewing_index
            if index < len(rows):
                item = rows[index]
                if event.type == 'X':
                    bpy.ops.clothnext.remove_sewing(index=index)
                elif event.type == 'E':
                    item.enabled = not item.enabled
                    bpy.ops.ed.undo_push(message='Toggle Sewing')
        if event.type in {'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS' and self.mode != 'EDIT':
            if self.mode == 'MULTI':
                if self.group_stage == 'A' and self.group_a:
                    self.group_stage = 'B'
                    self.stage = 'SELECT_SIDE_B'
                elif self.group_b and self.stage != 'PREVIEW':
                    self._multi_pairs()
                    self.stage = 'PREVIEW'
                    self.feedback = 'Enter commit ordered relationships | F reverse | Esc cancel'
                elif self.stage == 'PREVIEW':
                    pairs = self._multi_pairs()
                    # Validate all relationships before mutating the scene.
                    for (source, a), (target, b) in pairs:
                        sv, se, _ = self._cache(source)
                        tv, te, _ = self._cache(target)
                        validate_paths(sv, se, a, tv, te, b, source is target)
                    for (source, a), (target, b) in pairs:
                        _commit(self.scene, source, target, a, b, self.flipped)
                    bpy.ops.ed.undo_push(message='Create Multi Sewing')
                    self.reset()
            elif self.mode == 'FREE' and self.stage == 'SELECT_SIDE_A' and len(self.a) > 1:
                self.stage = 'SELECT_SIDE_B'
            elif self.mode == 'FREE' and self.stage == 'SELECT_SIDE_B' and len(self.b) > 1:
                self.stage = 'PREVIEW'
                self.feedback = 'Enter commit | F reverse | Backspace edit | Esc cancel'
            elif self.stage == 'PREVIEW':
                _commit(self.scene, self.source, self.target, self.a, self.b, self.flipped)
                bpy.ops.ed.undo_push(message='Create Sewing')
                self.reset()
            self._redraw()
        return {'RUNNING_MODAL'}

    def _multi_pairs(self):
        a, b = self.group_a, self.group_b
        if not a or not b:
            raise SewingError('Select paths on both sides')
        if len(a) == 1:
            return tuple((a[0], path) for path in b)
        if len(b) == 1:
            return tuple((path, b[0]) for path in a)
        if len(a) != len(b):
            raise SewingError('Ordered M:N requires equal group counts, or a single shared path')
        return tuple(zip(a, b))

    def finish(self):
        global _editor_view_handle, _editor_text_handle
        if self.finished:
            return
        self.finished = True
        _editor_sessions.pop(self.key, None)
        self.window.cursor_modal_restore()
        self._redraw()
        if not _editor_sessions:
            for handle in (_editor_view_handle, _editor_text_handle):
                if handle:
                    bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
            _editor_view_handle = _editor_text_handle = None

    def cancel(self, context):
        self.finish()


def _active_editor():
    context = bpy.context
    return _editor_sessions.get(context.window.as_pointer()) if context.window else None


def _editor_geometry(op):
    a, b = op.preview_paths()
    source = op.source or (op.hover[0] if op.hover else None)
    target = (op.hover[0] if op.hover and op.stage == 'SELECT_SIDE_B' and not op.b else op.target)
    first = tuple(op._cache(source)[0][i] for i in a) if source else ()
    second = tuple(op._cache(target)[0][i] for i in b) if target else ()
    return first, second


def _draw_editor():
    op = _active_editor()
    if op is None or not bpy.context.scene.cloth_next_show_sewing:
        return
    try:
        if op.mode == 'MULTI':
            for group in (op.group_a, op.group_b):
                for obj, path in group:
                    renderer.draw(tuple(op._cache(obj)[0][i] for i in path), (), 'active-seam', selected=True)
            if op.group_a and op.group_b:
                for (source, a), (target, b) in op._multi_pairs():
                    renderer.draw(tuple(op._cache(source)[0][i] for i in a), tuple(op._cache(target)[0][i] for i in b), 'active-seam', selected=True, flipped=op.flipped)
            return
        a, b = _editor_geometry(op)
        renderer.draw(a, b, 'active-seam', selected=True, flipped=op.flipped, invalid=op.invalid)
    except Exception:
        _log.exception('Sewing preview draw failed')


def _draw_editor_text():
    import blf
    context = bpy.context
    if not context.scene.cloth_next_show_sewing:
        return
    op = _active_editor()
    try:
        for first, second, identity, selected, flipped in _overlay_paths(context.scene):
            renderer.notches(first, second, identity, flipped)
        if op is None:
            return
        a, b = _editor_geometry(op)
        renderer.notches(a, b, 'active-seam', op.flipped)
        blf.position(0, 28, 45, 0)
        blf.size(0, 14)
        blf.color(0, 1, .2 if op.invalid else 1, .2 if op.invalid else 1, 1)
        blf.draw(0, 'SEWING — ' + op.feedback)
        settings = context.scene.cloth_next_sewing_visual
        if settings.lengths and a and b:
            la, lb = length(a), length(b)
            diff, relative = abs(la-lb), abs(la-lb)/max(la, lb, 1e-12)
            warning = diff > settings.absolute and relative > settings.threshold
            unit = context.scene.unit_settings
            fmt = lambda v: bpy.utils.units.to_string(unit.system if unit.system != 'NONE' else 'METRIC', 'LENGTH', v * unit.scale_length, precision=3)
            blf.color(0, *( (1,.2,.2,1) if warning else (.4,.75,1,1)))
            blf.position(0, op.mouse[0]-context.region.x+18, op.mouse[1]-context.region.y+22, 0)
            blf.draw(0, f'A {fmt(la)}   B {fmt(lb)}   Δ {fmt(diff)} ({relative:.1%})')
    except Exception:
        _log.exception('Sewing labels draw failed')


_draw_handle = None
_text_handle = None


def _playback(scene):
    return bool(getattr(getattr(bpy.context, 'screen', None), 'is_animation_playing', False))


def _overlay_paths(scene):
    if not interaction_visible(scene.cloth_next_show_sewing, True, playback=_playback(scene), baking=shared_controller.snapshot().active):
        return
    objects = _objects(scene)
    for index, item in enumerate(scene.cloth_next_sewing_definitions):
        selected = index == scene.cloth_next_sewing_index
        if not item.show_overlay or (not selected and not scene.cloth_next_sewing_visual.inactive):
            continue
        source, target = objects.get(item.source_persistent_id), objects.get(item.target_persistent_id)
        if source is None or target is None:
            continue
        sv, se, st = _cached_mesh(source)
        tv, te, tt = _cached_mesh(target)
        a, b = tuple(r.index for r in item.side_a), tuple(r.index for r in item.side_b)
        try:
            validate_paths(sv, se, a, tv, te, b, source is target)
            if topology_fingerprint(len(sv), st) != item.source_topology or topology_fingerprint(len(tv), tt) != item.target_topology:
                continue
        except ValueError:
            continue
        yield tuple(sv[i] for i in a), tuple(tv[i] for i in b), item.identifier, selected and item.enabled, item.flipped


def _draw_overlay():
    try:
        for a, b, identity, selected, flipped in _overlay_paths(bpy.context.scene):
            renderer.draw(a, b, identity, selected=selected, flipped=flipped)
    except Exception:
        _log.exception('Sewing overlay draw failed')


def _ensure_draw_handler():
    global _draw_handle, _text_handle
    if _draw_handle is None and hasattr(bpy.types, 'SpaceView3D'):
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_overlay, (), 'WINDOW', 'POST_VIEW')
        _text_handle = bpy.types.SpaceView3D.draw_handler_add(_draw_editor_text, (), 'WINDOW', 'POST_PIXEL')


def register():
    _ensure_draw_handler()
    bpy.types.Scene.cloth_next_sewing_visual = bpy.props.PointerProperty(type=CLOTHNEXT_PG_sewing_visual)
    validation_state.add_depsgraph_observer(_invalidate)
    for handlers, callback in ((getattr(bpy.app.handlers, "undo_pre", []), _reset_editors),
                               (getattr(bpy.app.handlers, "redo_pre", []), _reset_editors),
                               (bpy.app.handlers.load_pre, _reset_editors)):
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    global _draw_handle, _text_handle
    for op in tuple(_editor_sessions.values()):
        op.finish()
    for handle in (_draw_handle, _text_handle):
        if handle:
            bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _draw_handle = _text_handle = None
    validation_state.remove_depsgraph_observer(_invalidate)
    for handlers, callback in ((getattr(bpy.app.handlers, "undo_pre", []), _reset_editors),
                               (getattr(bpy.app.handlers, "redo_pre", []), _reset_editors),
                               (bpy.app.handlers.load_pre, _reset_editors)):
        if callback in handlers:
            handlers.remove(callback)
    if hasattr(bpy.types.Scene, 'cloth_next_sewing_visual'):
        del bpy.types.Scene.cloth_next_sewing_visual
    _geometry.clear()
    renderer.clear()


CLASSES = (CLOTHNEXT_PG_sewing_vertex, CLOTHNEXT_PG_sewing_pair,
           CLOTHNEXT_PG_sewing_definition, CLOTHNEXT_PG_sewing_visual,
           CLOTHNEXT_OT_remove_sewing, CLOTHNEXT_OT_flip_sewing, CLOTHNEXT_OT_edit_sewing)
