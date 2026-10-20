# SPDX-License-Identifier: GPL-3.0-or-later
"""Marvelous-Designer-style path Sewing authoring and persistent overlay.

Responsibilities are split three ways:

* authored geometry: ``CLOTHNEXT_PG_sewing_definition`` rows (paths of
  original-mesh vertex indices plus persistent object identities);
* correspondence and validation: ``cloth_next.sewing`` and
  ``cloth_next.sewing_visual`` (pure, arc-length based);
* solver stitches: ``snapshot_enabled`` (GAIA six-slot ``cross_stitch``).

The editor and overlays only read cached geometry from ``viewport_pick``;
they never generate solver constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import uuid

import bpy

from .. import export_identity
from ..attachments import (AttachmentError, AttachmentPoint, ObjectAttachment,
                           topology_fingerprint)
from ..bake.controller import shared_controller
from ..sewing import (SewingError, interaction_visible, path_mapping,
                      sampled_path_mapping, sampled_target_point, solver_stitch_stiffness)
from .. import sewing_pick
from ..sewing_visual import segment, validate_paths, length
from . import validation_state, viewport_pick
from .sewing_overlay import PREVIEW, SeamGeometry, renderer
from .tool_overlay import SEPARATOR, ToolButton

_log = logging.getLogger(__name__)


class CLOTHNEXT_PG_sewing_vertex(bpy.types.PropertyGroup):
    index: bpy.props.IntProperty(default=0, min=0)


class CLOTHNEXT_PG_sewing_pair(bpy.types.PropertyGroup):
    source_index: bpy.props.IntProperty(default=0, min=0)
    target_index: bpy.props.IntProperty(default=0, min=0)


_revision = 0


def _bump():
    global _revision
    _revision += 1


def _settings_changed(_self, _context):
    _bump()
    validation_state.mark_all_settings_dirty()


def _display_changed(_self, _context):
    _bump()


class CLOTHNEXT_PG_sewing_definition(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(default="")
    name: bpy.props.StringProperty(default="Sewing")
    enabled: bpy.props.BoolProperty(default=True, update=_settings_changed)
    show_overlay: bpy.props.BoolProperty(default=True, update=_display_changed)
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
        description="Seam pull strength exported to the solver (100 is a firm production seam)",
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
    return sewing_pick.boundary_edges(polygon.vertices for polygon in obj.data.polygons)


def _fill(collection, values):
    collection.clear()
    for value in values:
        collection.add().index = int(value)


def _sides(item):
    return (tuple(row.index for row in item.side_a), tuple(row.index for row in item.side_b))


def _rebuild_mapping(item, source, target):
    source_vertices, _, _ = _mesh(source)
    target_vertices, _, _ = _mesh(target)
    side_a, side_b = _sides(item)
    mapping = path_mapping(source_vertices, side_a, target_vertices, side_b,
                           flipped=bool(item.flipped))
    item.mapping.clear()
    for source_index, target_index in mapping:
        row = item.mapping.add()
        row.source_index, row.target_index = source_index, target_index
    _bump()


def _validate(source_geometry, side_a, target_geometry, side_b, same):
    try:
        validate_paths(source_geometry.rest_tuple, source_geometry.edge_set, side_a,
                       target_geometry.rest_tuple, target_geometry.edge_set, side_b, same)
    except ValueError as exc:
        raise SewingError(str(exc)) from exc


def _unique_name(scene, source, target):
    base = (f"{source.name} → {target.name}" if source is not target
            else f"{source.name} seam")
    names = {item.name for item in scene.cloth_next_sewing_definitions}
    if base not in names and source is not target:
        return base
    index = 1
    while f"{base} {index}" in names:
        index += 1
    return f"{base} {index}"


def _commit(scene, source, target, side_a, side_b, flipped=False):
    export_identity.ensure_unique_persistent_ids(scene.objects)
    side_a, side_b = tuple(map(int, side_a)), tuple(map(int, side_b))
    # Cached world geometry: committing on a dense garment must not rescan
    # the mesh in Python. Export still re-derives everything from the mesh.
    # Scripts may edit a mesh without a depsgraph update in between.
    viewport_pick.invalidate(source)
    viewport_pick.invalidate(target)
    source_geometry = viewport_pick.geometry(source)
    target_geometry = source_geometry if target is source else viewport_pick.geometry(target)
    _validate(source_geometry, side_a, target_geometry, side_b, source is target)
    mapping = path_mapping(source_geometry.rest_tuple, side_a, target_geometry.rest_tuple,
                           side_b, flipped=flipped)
    item = scene.cloth_next_sewing_definitions.add()
    item.identifier = uuid.uuid4().hex
    item.name = _unique_name(scene, source, target)
    item.source_name, item.target_name = source.name, target.name
    item.source_persistent_id = source.cloth_next.persistent_export_id
    item.target_persistent_id = target.cloth_next.persistent_export_id
    item.source_topology = source_geometry.fingerprint
    item.target_topology = target_geometry.fingerprint
    item.flipped = bool(flipped)
    _fill(item.side_a, side_a)
    _fill(item.side_b, side_b)
    for source_index, target_index in mapping:
        row = item.mapping.add()
        row.source_index, row.target_index = source_index, target_index
    scene.cloth_next_sewing_index = len(scene.cloth_next_sewing_definitions) - 1
    _bump()
    validation_state.mark_all_settings_dirty()
    _ensure_draw_handler()
    return item


def _incident_triangles(obj):
    obj.data.calc_loop_triangles()
    incident = {}
    for tri in obj.data.loop_triangles:
        triangle = tuple(map(int, tri.vertices))
        for a, b in zip(triangle, triangle[1:] + triangle[:1]):
            incident.setdefault(tuple(sorted((a, b))), triangle)
    return incident


def _attachment_points(name, samples, incident, source_vertices, target_vertices, same):
    points = []
    for source_index, a, b, fraction in samples:
        # A shared corner of a same-object seam maps onto itself: no constraint.
        if same and ((source_index == a and fraction <= 1e-10)
                     or (source_index == b and fraction >= 1.0 - 1e-10)):
            continue
        triangle = incident.get(tuple(sorted((a, b))))
        if triangle is None:
            raise SewingError(f"{name}: sewing path is not on a surface")
        weights = tuple((1.0 - fraction if i == a else
                         fraction if i == b else 0.0) for i in triangle)
        points.append(AttachmentPoint(
            source_index, triangle, weights,
            source_vertices[source_index],
            sampled_target_point(target_vertices, a, b, fraction)))
    return points


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
        # and cross-object definitions. Both endpoints remain live simulated
        # vertices; source_points/target_points are diagnostics, not pins.
        same = source is target
        side_a, side_b = _sides(item)
        source_vertices, _, _ = _mesh(source)
        target_vertices = source_vertices if same else _mesh(target)[0]
        incident = _incident_triangles(target)
        samples = sampled_path_mapping(source_vertices, side_a, target_vertices, side_b,
                                       flipped=bool(item.flipped))
        points = _attachment_points(item.name, samples, incident,
                                    source_vertices, target_vertices, same)
        if not points:
            raise SewingError(f"{item.name}: the seam has no distinct vertex pairs")
        stitches.append((ObjectAttachment(
            item.identifier, item.name, export_identity.export_uuid(source),
            export_identity.export_uuid(target), "CLOTH", "CLOTH",
            stiffness, tuple(points), allow_same_object=True),
            len(source_entry.boundary_vertices), len(target_entry.boundary_vertices)))
        # Also constrain target vertices that lie between source samples.
        # The six-slot protocol has a vertex on its source side, so these
        # symmetric rows are an explicit reverse attachment, not world pins.
        covered = {a if fraction <= 1e-10 else b for _, a, b, fraction in samples
                   if fraction <= 1e-10 or fraction >= 1 - 1e-10}
        reverse_samples = tuple(row for row in sampled_path_mapping(
            target_vertices, side_b, source_vertices, side_a,
            flipped=bool(item.flipped)) if row[0] not in covered)
        if reverse_samples:
            reverse_points = _attachment_points(
                item.name, reverse_samples, incident if same else _incident_triangles(source),
                target_vertices, source_vertices, same)
            if reverse_points:
                stitches.append((ObjectAttachment(
                    item.identifier + "_reverse", item.name,
                    export_identity.export_uuid(target), export_identity.export_uuid(source),
                    "CLOTH", "CLOTH", stiffness, tuple(reverse_points),
                    allow_same_object=True), len(target_vertices), len(source_vertices)))
        item.status_message = "Ready"
    return ({}, tuple(stitches))


class CLOTHNEXT_OT_remove_sewing(bpy.types.Operator):
    bl_idname = "clothnext.remove_sewing"
    bl_label = "Remove Sewing"
    bl_description = "Delete this seam"
    bl_options = {"UNDO", "INTERNAL"}
    index: bpy.props.IntProperty(default=-1, options={"HIDDEN"})

    def execute(self, context):
        rows = context.scene.cloth_next_sewing_definitions
        index = self.index if self.index >= 0 else context.scene.cloth_next_sewing_index
        if not 0 <= index < len(rows):
            return {"CANCELLED"}
        rows.remove(index)
        context.scene.cloth_next_sewing_index = max(0, min(index, len(rows) - 1))
        _bump()
        validation_state.mark_all_settings_dirty()
        return {"FINISHED"}


class CLOTHNEXT_OT_flip_sewing(bpy.types.Operator):
    bl_idname = "clothnext.flip_sewing"
    bl_label = "Reverse Sewing Direction"
    bl_description = "Reverse which ends of the two sides are sewn together"
    bl_options = {"UNDO", "INTERNAL"}
    index: bpy.props.IntProperty(default=-1, options={"HIDDEN"})

    def execute(self, context):
        rows = context.scene.cloth_next_sewing_definitions
        index = self.index if self.index >= 0 else context.scene.cloth_next_sewing_index
        if not (0 <= index < len(rows)):
            return {"CANCELLED"}
        item, objects = rows[index], _objects(context.scene)
        source = objects.get(item.source_persistent_id)
        target = objects.get(item.target_persistent_id)
        if source is None or target is None:
            self.report({"WARNING"}, "Sewn object no longer exists")
            return {"CANCELLED"}
        item.flipped = not item.flipped
        _rebuild_mapping(item, source, target)
        validation_state.mark_all_settings_dirty()
        return {"FINISHED"}


class CLOTHNEXT_OT_select_sewing(bpy.types.Operator):
    bl_idname = "clothnext.select_sewing"
    bl_label = "Edit Sewing"
    bl_description = "Select this seam and open Edit Sewing in the 3D View"
    bl_options = {"INTERNAL"}
    index: bpy.props.IntProperty(default=0, options={"HIDDEN"})

    def execute(self, context):
        rows = context.scene.cloth_next_sewing_definitions
        if not 0 <= self.index < len(rows):
            return {"CANCELLED"}
        context.scene.cloth_next_sewing_index = self.index
        _bump()
        editor = _editor_for_window(context.window)
        if editor is not None:
            editor.set_mode("EDIT")
            editor._redraw()
            return {"FINISHED"}
        return _start_editor(context, "EDIT")


def _start_editor(context, mode):
    """Run the editor in a 3D View even when invoked from the Properties editor."""
    for area in context.window.screen.areas:
        if area.type == "VIEW_3D":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            with context.temp_override(window=context.window, area=area, region=region):
                return bpy.ops.clothnext.edit_sewing("INVOKE_DEFAULT", mode=mode)
    return {"CANCELLED"}


class CLOTHNEXT_PG_sewing_visual(bpy.types.PropertyGroup):
    lines: bpy.props.BoolProperty(name="Show Sewing Lines", default=True,
                                  description="Colored highlights on both seam sides")
    threads: bpy.props.BoolProperty(name="Show Threads", default=True,
                                    description="Fine correspondence lines between the sides")
    notches: bpy.props.BoolProperty(name="Show Directional Notches", default=True,
                                    description="Arrows showing which ends are sewn together")
    lengths: bpy.props.BoolProperty(name="Show Length Difference", default=True,
                                    description="Side lengths next to the cursor while sewing")
    inactive: bpy.props.BoolProperty(name="Show Inactive Seams", default=True,
                                     description="Also draw seams that are not selected")
    density: bpy.props.FloatProperty(name="Thread Density", default=1, min=.1, max=4)
    opacity: bpy.props.FloatProperty(name="Thread Opacity", default=.55, min=0, max=1,
                                     subtype="FACTOR")
    threshold: bpy.props.FloatProperty(name="Relative Warning", default=.1, min=0, max=1,
                                       subtype="FACTOR")
    absolute: bpy.props.FloatProperty(name="Absolute Warning", default=.01, min=0,
                                      subtype="DISTANCE", unit="LENGTH")
    xray: bpy.props.BoolProperty(name="See Through", default=False,
                                 description="Draw and pick seams through cloth and bodies")
    ui_expanded: bpy.props.BoolProperty(name="Display", default=False)


# ---------------------------------------------------------------------------
# Seam records: cached display geometry and validity of saved definitions.

@dataclass(frozen=True)
class SeamRecord:
    index: int
    identifier: str
    source: object
    target: object
    side_a: tuple
    side_b: tuple
    geometry: SeamGeometry
    valid: bool
    message: str
    enabled: bool


_records = {"key": None, "rows": ()}


def _seam_records(scene):
    definitions = getattr(scene, "cloth_next_sewing_definitions", ())
    objects = _objects(scene)
    signature = tuple((item.identifier, item.source_persistent_id, item.target_persistent_id,
                       item.enabled, item.flipped, len(item.side_a), len(item.side_b))
                      for item in definitions)
    pointers = []
    for _identifier, source_id, target_id, *_rest in signature:
        for identity in (source_id, target_id):
            obj = objects.get(identity)
            pointers.append(viewport_pick.geometry(obj).version
                            if obj is not None and _eligible(None, obj) else None)
    key = (scene.as_pointer(), _revision, signature, tuple(pointers))
    if _records["key"] == key:
        return _records["rows"]
    rows = []
    for index, item in enumerate(definitions):
        source = objects.get(item.source_persistent_id)
        target = objects.get(item.target_persistent_id)
        side_a, side_b = _sides(item)
        valid, message, first, second, version = False, "", (), (), (item.identifier,)
        if source is None or target is None:
            message = "Sewn object no longer exists"
        elif not (_eligible(None, source) and _eligible(None, target)):
            message = "Both sides must be Cloth NeXt Cloth objects"
        else:
            sg = viewport_pick.geometry(source)
            tg = sg if target is source else viewport_pick.geometry(target)
            version = (item.identifier, sg.version, tg.version, side_a, side_b, item.flipped)
            try:
                _validate(sg, side_a, tg, side_b, source is target)
                if (sg.fingerprint != item.source_topology
                        or tg.fingerprint != item.target_topology):
                    raise SewingError("Mesh topology changed; reassign the sides")
                valid = True
            except SewingError as exc:
                message = str(exc)
            if all(0 <= i < sg.vertex_count for i in side_a):
                first = tuple(sg.display_tuple[i] for i in side_a)
            if all(0 <= i < tg.vertex_count for i in side_b):
                second = tuple(tg.display_tuple[i] for i in side_b)
        rows.append(SeamRecord(index, item.identifier, source, target, side_a, side_b,
                               SeamGeometry(item.identifier, version, first, second,
                                            bool(item.flipped), not valid, message),
                               valid, message, bool(item.enabled)))
    rows = tuple(rows)
    _records["key"], _records["rows"] = key, rows
    return rows


def seam_status(scene, index):
    """(valid, message) of a saved seam for the panel.

    Draw-safe and mesh-free: it only reads what the viewport overlay last
    validated, falling back to the status recorded by the last export.
    """
    definitions = scene.cloth_next_sewing_definitions
    if not 0 <= index < len(definitions):
        return True, ""
    item = definitions[index]
    rows = _records["rows"] if _records["key"] is not None else ()
    if index < len(rows) and rows[index].identifier == item.identifier:
        return rows[index].valid, rows[index].message
    status = str(item.status_message or "")
    return not status.startswith("Needs Rebuild"), "" if status == "Ready" else status


# ---------------------------------------------------------------------------
# Editor

_editor_sessions = {}
_editor_view_handle = None
_editor_pixel_handle = None

MODES = (("SEGMENT", "Segment", "Click two boundary segments; corners end a segment"),
         ("FREE", "Free", "Click points along any edges; the route between points is shown before you click"),
         ("MULTI", "Multi", "Pair one path with several, or two ordered groups of paths"),
         ("EDIT", "Edit", "Select a saved seam to reverse, toggle, reassign or delete it"))
_NAVIGATION = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE",
               "WHEELOUTMOUSE", "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE",
               "MOUSESMARTZOOM", "TIMER", "TIMER_REPORT", "TIMERREGION"}


@dataclass(frozen=True)
class Hover:
    obj: object
    path: tuple
    vertex: int


def _editor_for_window(window):
    return _editor_sessions.get(window.as_pointer()) if window else None


def _invalidate_records():
    _records["key"] = None
    renderer.clear()


@validation_state.persistent
def _on_depsgraph(*args):
    if len(args) > 1 and hasattr(args[1], "updates"):
        if viewport_pick.invalidate_depsgraph(args[1]):
            renderer.clear()
        return
    viewport_pick.clear()
    _invalidate_records()


@validation_state.persistent
def _reset_editors(*_args):
    for op in tuple(_editor_sessions.values()):
        op.finish()
    viewport_pick.clear()
    _invalidate_records()


class CLOTHNEXT_OT_edit_sewing(bpy.types.Operator):
    bl_idname = "clothnext.edit_sewing"
    bl_label = "Sewing"
    bl_description = ("Sew edges in the 3D View. Stays active for any number of seams; "
                      "Esc cancels the current seam, Esc again exits")
    bl_options = {"INTERNAL", "BLOCKING"}
    mode: bpy.props.EnumProperty(items=tuple((key, label, description)
                                             for key, label, description in MODES),
                                 default="SEGMENT")
    reassign: bpy.props.EnumProperty(items=(("NONE", "None", ""), ("A", "Side A", ""),
                                            ("B", "Side B", "")), default="NONE")

    @classmethod
    def poll(cls, context):
        return (context.mode == "OBJECT" and not shared_controller.snapshot().active
                and any(_eligible(None, obj) for obj in context.scene.objects))

    def invoke(self, context, event):
        global _editor_view_handle, _editor_pixel_handle
        if context.window is None:
            return {"CANCELLED"}
        existing = _editor_for_window(context.window)
        if existing is not None:
            existing.set_mode(self.mode)
            existing._redraw()
            return {"CANCELLED"}
        self.window, self.scene = context.window, context.scene
        self.key = self.window.as_pointer()
        self.finished = False
        self.layouts = {}
        self.toolbar_hover = None
        self.hover_seam = None
        self.mouse = (event.mouse_x, event.mouse_y)
        self.mouse_region = None
        self.message = ""
        self.message_is_error = False
        self._route_cache = {}
        self.reset()
        _editor_sessions[self.key] = self
        try:
            if _editor_view_handle is None:
                _editor_view_handle = bpy.types.SpaceView3D.draw_handler_add(
                    _draw_editor, (), "WINDOW", "POST_VIEW")
                _editor_pixel_handle = bpy.types.SpaceView3D.draw_handler_add(
                    _draw_editor_pixels, (), "WINDOW", "POST_PIXEL")
            _ensure_draw_handler()
            context.window_manager.modal_handler_add(self)
        except Exception:
            self.finish()
            raise
        self._redraw()
        return {"RUNNING_MODAL"}

    # -- state -------------------------------------------------------------
    def reset(self):
        self.source = self.target = None
        self.a, self.b = [], []
        self.points_a, self.points_b = [], []
        self.hover = None
        self.flipped = False
        self.invalid = False
        self.stage = "IDLE"
        self.group_a, self.group_b = [], []
        self.group_stage = "A"
        self.message, self.message_is_error = "", False

    def set_mode(self, mode):
        if mode not in {key for key, *_ in MODES}:
            return
        self.mode = mode
        self.reassign = "NONE"
        self.reset()

    def _say(self, message, error=False):
        self.message, self.message_is_error = message, error
        self.invalid = error

    def hint(self):
        if self.message:
            return self.message
        if self.mode == "EDIT":
            if self.reassign != "NONE":
                return f"Click the new boundary segment for Side {self.reassign}"
            return "Click a seam to select it  ·  F reverse  ·  E on/off  ·  X delete  ·  Esc exit"
        if self.mode == "MULTI":
            group = "first group" if self.group_stage == "A" else "second group"
            if self.stage == "PREVIEW":
                return "Enter sews all pairs  ·  F reverse  ·  Esc cancel"
            return f"Click paths of the {group} in order  ·  Enter next  ·  Backspace removes last"
        if self.stage == "PREVIEW":
            return "Enter sews  ·  F reverse  ·  Backspace edits Side B  ·  Esc cancels"
        side = "A" if self.stage in {"IDLE", "SELECT_SIDE_A"} else "B"
        if self.mode == "FREE":
            if (self.points_a if side == "A" else self.points_b):
                return (f"Click more points for Side {side}  ·  Enter finishes the side  ·  "
                        "Backspace removes the last point")
            return f"Click the start point of Side {side}"
        return f"Click the boundary segment for Side {side}  ·  F reverse  ·  Esc cancel"

    def toolbar_items(self):
        settings = self.scene.cloth_next_sewing_visual
        icons = {"SEGMENT": "segment", "FREE": "free", "MULTI": "multi", "EDIT": "edit"}
        items = [ToolButton("mode:" + key, label + " Sewing", "mode", active=self.mode == key,
                            tooltip=f"{description} ({number})", icon=icons[key])
                 for number, (key, label, description) in enumerate(MODES, 1)]
        items.append(SEPARATOR)
        if self.mode == "EDIT":
            item = self._selected_item()
            has = item is not None
            items += [
                ToolButton("reverse", "Reverse Direction", enabled=has, tooltip="F",
                           icon="reverse"),
                ToolButton("enable", "Enabled" if (has and item.enabled) else "Disabled",
                           "toggle", active=bool(has and item.enabled), enabled=has,
                           tooltip="Enable or disable this seam (E)", icon="enable"),
                ToolButton("side_a", "A", "toggle", active=self.reassign == "A", enabled=has,
                           tooltip="Reassign Side A to another segment (A)"),
                ToolButton("side_b", "B", "toggle", active=self.reassign == "B", enabled=has,
                           tooltip="Reassign Side B to another segment (B)"),
                ToolButton("strength_up", "Strength +10", enabled=has, icon="plus"),
                ToolButton("strength", f"{item.strength:.0f}" if has else "–", "value",
                           tooltip="Strength"),
                ToolButton("strength_down", "Strength −10", enabled=has, icon="minus"),
                ToolButton("delete", "Delete Seam", "danger", enabled=has, tooltip="X",
                           icon="delete"),
            ]
        else:
            busy = self.stage != "IDLE" or bool(self.group_a)
            items += [
                ToolButton("reverse", "Reverse Direction", tooltip="F", icon="reverse"),
                ToolButton("back", "Undo Point", enabled=busy,
                           tooltip="Remove the last point or path (Backspace)", icon="back"),
                ToolButton("cancel", "Cancel Seam", enabled=busy, tooltip="Esc",
                           icon="cancel"),
                ToolButton("confirm", self._confirm_label(), "primary",
                           enabled=self._can_confirm(), tooltip="Enter", icon="confirm"),
            ]
        items += [SEPARATOR,
                  ToolButton("toggle:threads", "Show Threads", "toggle", active=settings.threads,
                             icon="threads"),
                  ToolButton("toggle:xray", "X-Ray", "toggle", active=settings.xray,
                             tooltip="Pick and draw through cloth and bodies", icon="xray"),
                  SEPARATOR,
                  ToolButton("done", "Exit Sewing", tooltip="Esc when idle", icon="close")]
        return items

    def _confirm_label(self):
        if self.mode == "MULTI":
            if self.stage == "PREVIEW":
                return "Sew All"
            return "Next Group" if self.group_stage == "A" else "Preview"
        if self.stage == "PREVIEW":
            return "Sew"
        if self.mode == "FREE":
            return "Finish Side " + ("A" if self.stage in {"IDLE", "SELECT_SIDE_A"} else "B")
        return "Sew"

    def _can_confirm(self):
        if self.mode == "MULTI":
            if self.stage == "PREVIEW":
                return not self.invalid
            return bool(self.group_a if self.group_stage == "A" else self.group_b)
        if self.stage == "PREVIEW":
            return not self.invalid
        if self.mode == "FREE":
            return len(self.a if self.stage in {"IDLE", "SELECT_SIDE_A"} else self.b) > 1
        return False

    # -- geometry and picking ----------------------------------------------
    def _candidates(self, context):
        result = []
        for obj in self.scene.objects:
            try:
                if _eligible(None, obj) and obj.visible_get(view_layer=context.view_layer):
                    result.append(obj)
            except (ReferenceError, RuntimeError):
                continue
        return result

    def _view(self, context, area, region):
        return viewport_pick.ViewState(region, area.spaces.active.region_3d)

    def _pick(self, context, event):
        found = viewport_pick.region_under_mouse(self.window, event)
        if found is None:
            return None
        area, region, mouse = found
        layout = self.layouts.get(region.as_pointer())
        if layout is not None and layout.contains(*mouse):
            return None
        view = self._view(context, area, region)
        rv3d = view.rv3d
        from bpy_extras import view3d_utils
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
        objects = self._candidates(context)
        geometries = {obj: viewport_pick.geometry(obj) for obj in objects}
        pointers = {obj.as_pointer() for obj in objects}
        segment_mode = self.mode in {"SEGMENT", "MULTI"} or self.reassign != "NONE"
        candidates = []
        for obj, entry in geometries.items():
            if segment_mode:
                for distance, edge, t in entry.edge_projection(view, "boundary").nearest(mouse):
                    candidates.append((distance, obj, edge, t))
            else:
                projected = entry.projection(view)["points"]
                for distance, index in sewing_pick.nearest_vertices(projected, None, mouse):
                    candidates.append((distance, obj, (index, index), 0.0))
        candidates.sort(key=lambda row: (row[0], row[1].name, row[2]))
        xray = self.scene.cloth_next_sewing_visual.xray
        for distance, obj, (a, b), t in candidates[:10]:
            entry = geometries[obj]
            point = tuple(entry.display[a] * (1 - t) + entry.display[b] * t)
            if not viewport_pick.is_visible(context, self.scene, point, origin,
                                            tuple(geometries.values()), pointers, xray=xray):
                continue
            if segment_mode:
                path = segment(entry.rest_tuple, (), (a, b),
                               neighbours=entry.boundary_neighbours)
                return Hover(obj, path, a if t < .5 else b)
            return Hover(obj, (a,), a)
        return None

    def _free_route(self, obj, points, vertex):
        """Path through explicit points plus the previewed route to ``vertex``."""
        path = list(self._free_path(obj, points))
        if vertex is None or not path or vertex == path[-1]:
            return tuple(path)
        return self._free_route_hop(obj, path, vertex)

    def _free_path(self, obj, points):
        """Expand explicit Free Sewing points; cached hops never change silently."""
        if not points:
            return ()
        path = [points[0]]
        for vertex in points[1:]:
            routed = self._free_route_hop(obj, path, vertex)
            if routed is None:
                break
            path = list(routed)
        return tuple(path)

    def _free_route_hop(self, obj, path, vertex):
        entry = viewport_pick.geometry(obj)
        key = ("hop", obj.as_pointer(), entry.version, tuple(path), vertex)
        cached = self._route_cache.get(key)
        if cached is None:
            forbidden = frozenset(path[:-1])
            vertices = entry.rest_tuple
            hop = None
            if path[-1] in entry.boundary_vertices and vertex in entry.boundary_vertices:
                hop = sewing_pick.route(vertices, entry.boundary_neighbours, path[-1], vertex,
                                        forbidden=forbidden, limit=20_000)
            if hop is None:
                hop = sewing_pick.route(vertices, entry.neighbours, path[-1], vertex,
                                        forbidden=forbidden, limit=60_000)
            cached = False if hop is None or vertex in forbidden else tuple(path) + hop[1:]
            if len(self._route_cache) > 256:
                self._route_cache.clear()
            self._route_cache[key] = cached
        return cached or None

    def preview_paths(self):
        a, b = tuple(self.a), tuple(self.b)
        hover = self.hover
        if hover is None or self.stage == "PREVIEW":
            return a, b
        side_a = self.stage in {"IDLE", "SELECT_SIDE_A"}
        if self.mode == "SEGMENT":
            if side_a:
                a = hover.path
            else:
                b = hover.path
        elif self.mode == "FREE":
            owner = self.source if side_a else self.target
            points = self.points_a if side_a else self.points_b
            if not points:
                if side_a:
                    a = (hover.vertex,)
                else:
                    b = (hover.vertex,)
            elif owner is hover.obj:
                routed = self._free_route(owner, points, hover.vertex)
                if routed:
                    if side_a:
                        a = routed
                    else:
                        b = routed
        return a, b

    def _preview_objects(self):
        hover_obj = self.hover.obj if self.hover else None
        source = self.source or (hover_obj if self.stage in {"IDLE", "SELECT_SIDE_A"} else None)
        target = self.target or (hover_obj if self.stage == "SELECT_SIDE_B" else None)
        return source, target

    def _live_validation(self):
        """Explain an invalid preview in red before the user commits."""
        if self.mode in {"EDIT", "MULTI"}:
            return
        source, target = self._preview_objects()
        a, b = self.preview_paths()
        if source is None or target is None or len(a) < 2 or len(b) < 2:
            if self.message_is_error and self.stage != "PREVIEW":
                self.message, self.message_is_error, self.invalid = "", False, False
            return
        try:
            _validate(viewport_pick.geometry(source), a, viewport_pick.geometry(target), b,
                      source is target)
        except SewingError as exc:
            self._say(str(exc), True)
        else:
            if self.message_is_error:
                self.message, self.message_is_error = "", False
            self.invalid = False

    def _redraw(self):
        from .linked_colliders import redraw
        redraw(self.window)

    # -- input -------------------------------------------------------------
    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except (ReferenceError, RuntimeError, ValueError, AttachmentError) as exc:
            self._say(str(exc), True)
            _log.warning("Sewing interaction: %s", exc)
            try:
                self._redraw()
            except ReferenceError:
                pass
            return {"RUNNING_MODAL"}

    def _alive(self, context):
        try:
            return (not self.finished and context.window == self.window
                    and self.window.scene == self.scene
                    and not shared_controller.snapshot().active
                    and not context.screen.is_animation_playing
                    and context.mode == "OBJECT")
        except (ReferenceError, AttributeError):
            return False

    def _modal(self, context, event):
        if self.finished:
            return {"FINISHED"}
        if not self._alive(context):
            self.finish()
            return {"CANCELLED"}
        self.mouse = (event.mouse_x, event.mouse_y)
        if not self._objects_alive():
            self.reset()
            self._say("A sewn object was removed; the seam was cancelled", True)
        if event.type in _NAVIGATION or event.type.startswith("NDOF") or (
                event.type.startswith("NUMPAD") and event.type != "NUMPAD_ENTER") or (
                event.alt and event.type == "LEFTMOUSE"):
            return {"PASS_THROUGH"}
        found = viewport_pick.region_under_mouse(self.window, event)
        if found is None:
            # Properties editor, sidebars and headers keep working.
            if self.hover is not None or self.toolbar_hover is not None:
                self.hover = self.toolbar_hover = None
                self._redraw()
            if event.type == "ESC" and event.value == "PRESS":
                return self._escape()
            return {"PASS_THROUGH"}
        area, region, mouse = found
        self.mouse_region = (region.as_pointer(), mouse)
        layout = self.layouts.get(region.as_pointer())
        over_toolbar = layout is not None and layout.contains(*mouse)
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            if event.type == "INBETWEEN_MOUSEMOVE":
                return {"RUNNING_MODAL"}
            hovered = layout.hit(*mouse) if over_toolbar else None
            changed = hovered != self.toolbar_hover
            self.toolbar_hover = hovered
            if over_toolbar:
                if self.hover is not None:
                    self.hover, changed = None, True
                self.window.cursor_modal_set("DEFAULT")
            else:
                self.window.cursor_modal_set("CROSSHAIR")
                previous = (self.hover, self.hover_seam)
                if self.mode == "EDIT" and self.reassign == "NONE":
                    self.hover = None
                    self.hover_seam = self._pick_seam(context, area, region, mouse)
                else:
                    self.hover = self._pick(context, event)
                    self._live_validation()
                changed = changed or previous != (self.hover, self.hover_seam)
            if changed:
                self._redraw()
            return {"RUNNING_MODAL"}
        if event.value != "PRESS":
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and over_toolbar:
            button = layout.hit(*mouse)
            if button:
                self._button(context, button)
                self._redraw()
            return {"RUNNING_MODAL"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            return self._escape()
        if event.ctrl and event.type in {"Z", "Y"}:
            if self.stage == "IDLE" and not self.group_a:
                # Undo invalidates editor references; leave cleanly first.
                self.finish()
                return {"FINISHED", "PASS_THROUGH"}
            return {"RUNNING_MODAL"}
        handled = True
        if event.type == "F":
            self._reverse()
        elif event.type == "BACK_SPACE":
            self._back()
        elif event.type in {"RET", "NUMPAD_ENTER"}:
            self._confirm()
        elif event.type == "LEFTMOUSE":
            self._click(context, event, area, region, mouse)
        elif self.mode == "EDIT" and event.type in {"X", "DEL", "E", "A", "B"}:
            self._edit_key(event.type)
        elif event.type in {"ONE", "TWO", "THREE", "FOUR"}:
            self.set_mode(MODES[("ONE", "TWO", "THREE", "FOUR").index(event.type)][0])
        else:
            handled = False
        if handled:
            self._redraw()
        return {"RUNNING_MODAL"}

    def _objects_alive(self):
        objects = self.scene.objects
        for obj in (self.source, self.target, *(o for o, _p in self.group_a),
                    *(o for o, _p in self.group_b)):
            if obj is None:
                continue
            try:
                if objects.get(obj.name) is not obj:
                    return False
            except ReferenceError:
                return False
        return True

    def _escape(self):
        if self.mode == "EDIT" and self.reassign != "NONE":
            self.reassign = "NONE"
            self.message = ""
        elif self.stage == "IDLE" and not self.group_a and not self.group_b:
            self.finish()
            return {"FINISHED"}
        else:
            self.reset()
            self._say("Seam cancelled")
        self._redraw()
        return {"RUNNING_MODAL"}

    def _button(self, context, button):
        settings = self.scene.cloth_next_sewing_visual
        if button.startswith("mode:"):
            self.set_mode(button[5:])
        elif button == "toggle:threads":
            settings.threads = not settings.threads
        elif button == "toggle:xray":
            settings.xray = not settings.xray
        elif button == "done":
            self.finish()
        elif button == "reverse":
            self._reverse()
        elif button == "back":
            self._back()
        elif button == "cancel":
            self.reset()
            self._say("Seam cancelled")
        elif button == "confirm":
            self._confirm()
        elif button in {"enable", "delete"}:
            self._edit_key({"enable": "E", "delete": "X"}[button])
        elif button in {"side_a", "side_b"}:
            side = button[-1].upper()
            self.reassign = "NONE" if self.reassign == side else side
            self.message = ""
        elif button in {"strength_down", "strength_up"}:
            item = self._selected_item()
            if item is not None:
                item.strength = max(0.0, item.strength + (10.0 if button == "strength_up" else -10.0))
                bpy.ops.ed.undo_push(message="Sewing Strength")

    def _selected_item(self):
        rows = self.scene.cloth_next_sewing_definitions
        index = self.scene.cloth_next_sewing_index
        return rows[index] if 0 <= index < len(rows) else None

    def _reverse(self):
        if self.mode == "EDIT":
            if self._selected_item() is not None:
                bpy.ops.clothnext.flip_sewing(index=self.scene.cloth_next_sewing_index)
                bpy.ops.ed.undo_push(message="Reverse Sewing")
                self._say("Direction reversed")
            return
        self.flipped = not self.flipped
        self._say("Direction reversed")

    def _back(self):
        self.message, self.message_is_error, self.invalid = "", False, False
        if self.mode == "MULTI":
            if self.stage == "PREVIEW":
                self.stage = "SELECT_SIDE_B"
                return
            group = self.group_a if self.group_stage == "A" else self.group_b
            if group:
                group.pop()
            elif self.group_stage == "B":
                self.group_stage, self.stage = "A", "SELECT_SIDE_A"
            if not self.group_a and not self.group_b:
                self.stage = "IDLE"
            return
        if self.mode == "FREE":
            if self.stage in {"SELECT_SIDE_B", "PREVIEW"}:
                if self.stage == "PREVIEW":
                    self.stage = "SELECT_SIDE_B"
                if self.points_b:
                    self.points_b.pop()
                    self.b = list(self._free_path(self.target, self.points_b)) if self.points_b else []
                    if not self.points_b:
                        self.target = None
                    return
                self.stage = "SELECT_SIDE_A"
            if self.points_a:
                self.points_a.pop()
                self.a = list(self._free_path(self.source, self.points_a)) if self.points_a else []
            if not self.points_a:
                self.reset()
            return
        # Segment: step back one side.
        if self.stage == "PREVIEW":
            self.b, self.target, self.stage = [], None, "SELECT_SIDE_B"
        elif self.stage == "SELECT_SIDE_B":
            self.reset()

    def _confirm(self):
        if self.mode == "EDIT":
            return
        if self.mode == "MULTI":
            if self.group_stage == "A" and self.group_a:
                self.group_stage, self.stage = "B", "SELECT_SIDE_B"
            elif self.group_stage == "B" and self.group_b and self.stage != "PREVIEW":
                self._multi_pairs()
                self.stage = "PREVIEW"
                self._validate_multi()
            elif self.stage == "PREVIEW":
                pairs = self._multi_pairs()
                self._validate_multi(raise_error=True)
                for (source, a), (target, b) in pairs:
                    _commit(self.scene, source, target, a, b, self.flipped)
                bpy.ops.ed.undo_push(message="Create Multi Sewing")
                count = len(pairs)
                self.reset()
                self._say(f"Created {count} seams")
            return
        if self.mode == "FREE" and self.stage == "SELECT_SIDE_A":
            if len(self.a) > 1:
                self.stage = "SELECT_SIDE_B"
                self.message = ""
            return
        if self.mode == "FREE" and self.stage == "SELECT_SIDE_B":
            if len(self.b) > 1:
                self.stage = "PREVIEW"
                self._live_validation()
            return
        if self.stage == "PREVIEW":
            if self.invalid:
                return
            item = _commit(self.scene, self.source, self.target, self.a, self.b, self.flipped)
            bpy.ops.ed.undo_push(message="Create Sewing")
            name = item.name
            self.reset()
            self._say(f"Created {name}  ·  click the next Side A")

    def _click(self, context, event, area, region, mouse):
        if self.mode == "EDIT" and self.reassign == "NONE":
            index = self._pick_seam(context, area, region, mouse)
            if index is None:
                self._say("Click directly on a seam side")
                return
            self.scene.cloth_next_sewing_index = index
            rows = self.scene.cloth_next_sewing_definitions
            rows[index].ui_expanded = True
            _bump()
            self.message = ""
            return
        self.hover = self._pick(context, event)
        if self.hover is None:
            return
        hover = self.hover
        if self.mode == "EDIT":
            self._reassign(hover)
            return
        if self.mode == "MULTI":
            if self.stage == "PREVIEW":
                return
            group = self.group_a if self.group_stage == "A" else self.group_b
            entry = (hover.obj, hover.path)
            if entry in group:
                self._say("This path is already in the group", True)
                return
            group.append(entry)
            self.stage = "SELECT_SIDE_A" if self.group_stage == "A" else "SELECT_SIDE_B"
            self.message = ""
            return
        if self.stage == "PREVIEW":
            return
        side_a = self.stage in {"IDLE", "SELECT_SIDE_A"}
        if self.mode == "SEGMENT":
            if not side_a:
                self.b = list(hover.path)
                self.target = hover.obj
                self.stage = "PREVIEW"
                self._live_validation()
                if not self.invalid:
                    self.message = ""
                return
            self.a = list(hover.path)
            self.source = hover.obj
            self.stage = "SELECT_SIDE_B"
            self.message = ""
            return
        # Free Sewing: explicit points; every point stays on the path.
        owner = self.source if side_a else self.target
        points = self.points_a if side_a else self.points_b
        if owner is not None and owner is not hover.obj:
            self._say("Finish this side on the same object first (Enter)", True)
            return
        if points and hover.vertex == points[-1]:
            return
        if points:
            routed = self._free_route(hover.obj, points, hover.vertex)
            if not routed:
                self._say("No route to this point without reusing the path", True)
                return
        points.append(hover.vertex)
        if side_a:
            self.source = hover.obj
            self.a = list(self._free_path(self.source, self.points_a))
            self.stage = "SELECT_SIDE_A"
        else:
            self.target = hover.obj
            self.b = list(self._free_path(self.target, self.points_b))
            self.stage = "SELECT_SIDE_B"
        self.message, self.message_is_error, self.invalid = "", False, False

    def _reassign(self, hover):
        item = self._selected_item()
        if item is None:
            return
        objects = _objects(self.scene)
        source = objects.get(item.source_persistent_id)
        target = objects.get(item.target_persistent_id)
        a, b = _sides(item)
        if self.reassign == "A":
            source, a = hover.obj, hover.path
        else:
            target, b = hover.obj, hover.path
        if source is None or target is None:
            self._say("The other side's object no longer exists", True)
            return
        sv, se, st = _mesh(source)
        tv, te, tt = (sv, se, st) if target is source else _mesh(target)
        try:
            validate_paths(sv, se, a, tv, te, b, source is target)
        except ValueError as exc:
            self._say(str(exc), True)
            return
        export_identity.ensure_unique_persistent_ids(self.scene.objects)
        item.source_persistent_id = source.cloth_next.persistent_export_id
        item.target_persistent_id = target.cloth_next.persistent_export_id
        item.source_name, item.target_name = source.name, target.name
        item.source_topology = topology_fingerprint(len(sv), st)
        item.target_topology = topology_fingerprint(len(tv), tt)
        _fill(item.side_a, a)
        _fill(item.side_b, b)
        _rebuild_mapping(item, source, target)
        validation_state.mark_all_settings_dirty()
        bpy.ops.ed.undo_push(message="Reassign Sewing Side")
        self._say(f"Side {self.reassign} reassigned")
        self.reassign = "NONE"

    def _edit_key(self, key):
        item = self._selected_item()
        if item is None:
            return
        index = self.scene.cloth_next_sewing_index
        if key in {"X", "DEL"}:
            name = item.name
            bpy.ops.clothnext.remove_sewing(index=index)
            bpy.ops.ed.undo_push(message="Delete Sewing")
            self._say(f"Deleted {name}")
        elif key == "E":
            item.enabled = not item.enabled
            bpy.ops.ed.undo_push(message="Toggle Sewing")
            self._say(f"{item.name} {'enabled' if item.enabled else 'disabled'}")
        elif key in {"A", "B"}:
            self.reassign = key
            self.message = ""

    def _pick_seam(self, context, area, region, mouse):
        view = self._view(context, area, region)
        best = None
        for record in _seam_records(self.scene):
            item = self.scene.cloth_next_sewing_definitions[record.index]
            if not item.show_overlay:
                continue
            for points in (record.geometry.first, record.geometry.second):
                if len(points) < 2:
                    continue
                projected = sewing_pick.project(view.matrix, points, view.width, view.height)
                distance = sewing_pick.polyline_distance(projected, mouse)
                if distance <= 10 and (best is None or distance < best[0]):
                    best = (distance, record.index)
        return None if best is None else best[1]

    def _multi_pairs(self):
        a, b = self.group_a, self.group_b
        if not a or not b:
            raise SewingError("Select paths on both sides")
        if len(a) == 1:
            return tuple((a[0], path) for path in b)
        if len(b) == 1:
            return tuple((path, b[0]) for path in a)
        if len(a) != len(b):
            raise SewingError("Ordered M:N needs equally many paths per group, "
                              "or a single shared path")
        return tuple(zip(a, b))

    def _validate_multi(self, raise_error=False):
        try:
            pairs = self._multi_pairs()
            for (source, a), (target, b) in pairs:
                _validate(viewport_pick.geometry(source), a, viewport_pick.geometry(target), b,
                          source is target)
        except SewingError as exc:
            self._say(str(exc), True)
            if raise_error:
                raise
            return False
        self.invalid = False
        return True

    def finish(self):
        global _editor_view_handle, _editor_pixel_handle
        if self.finished:
            return
        self.finished = True
        _editor_sessions.pop(self.key, None)
        try:
            self.window.cursor_modal_restore()
            self._redraw()
        except ReferenceError:
            pass
        if not _editor_sessions:
            for handle in (_editor_view_handle, _editor_pixel_handle):
                if handle is not None:
                    bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
            _editor_view_handle = _editor_pixel_handle = None

    def cancel(self, context):
        self.finish()


def _active_editor():
    context = bpy.context
    op = _editor_for_window(context.window)
    if op is None or op.finished:
        return None
    return op


def _view_for_context(context):
    region, rv3d = context.region, context.region_data
    if region is None or rv3d is None:
        return None
    return viewport_pick.ViewState(region, rv3d)


def _editor_seams(op):
    """SeamGeometry rows for the live preview."""
    if op.mode == "EDIT":
        return ()
    if op.mode == "MULTI":
        result = []
        for group in (op.group_a, op.group_b):
            for obj, path in group:
                entry = viewport_pick.geometry(obj)
                result.append(SeamGeometry(PREVIEW, ("group", entry.version, path),
                                           tuple(entry.display_tuple[i] for i in path), ()))
        hover = op.hover
        if hover is not None and op.stage != "PREVIEW":
            entry = viewport_pick.geometry(hover.obj)
            result.append(SeamGeometry("hover", ("hover", entry.version, hover.path),
                                       tuple(entry.display_tuple[i] for i in hover.path), ()))
        if op.group_a and op.group_b:
            try:
                pairs = op._multi_pairs()
            except SewingError:
                pairs = ()
            for number, ((source, a), (target, b)) in enumerate(pairs):
                sg, tg = viewport_pick.geometry(source), viewport_pick.geometry(target)
                result.append(SeamGeometry(
                    f"multi-{number}", ("multi", sg.version, tg.version, a, b),
                    tuple(sg.display_tuple[i] for i in a), tuple(tg.display_tuple[i] for i in b),
                    op.flipped, op.invalid))
        return tuple(result)
    a, b = op.preview_paths()
    source, target = op._preview_objects()
    if op.stage == "SELECT_SIDE_B" and op.hover is None:
        target = None
    first = second = ()
    sv = tv = None
    if source is not None and a:
        entry = viewport_pick.geometry(source)
        sv = entry.version
        first = tuple(entry.display_tuple[i] for i in a)
    if target is not None and b:
        entry = viewport_pick.geometry(target)
        tv = entry.version
        second = tuple(entry.display_tuple[i] for i in b)
    return (SeamGeometry(PREVIEW, ("preview", sv, tv, a, b, op.flipped), first, second,
                         op.flipped, op.invalid),)


def _draw_editor():
    op = _active_editor()
    if op is None or not bpy.context.scene.cloth_next_show_sewing:
        return
    context = bpy.context
    view = _view_for_context(context)
    if view is None:
        return
    try:
        for seam in _editor_seams(op):
            renderer.draw(context, view, seam, selected=True)
    except Exception:
        _log.exception("Sewing preview draw failed")


def _length_label(context, op, first, second, region_mouse):
    import blf
    settings = context.scene.cloth_next_sewing_visual
    if not settings.lengths or len(first) < 2 or len(second) < 2 or region_mouse is None:
        return
    la, lb = length(first), length(second)
    diff = abs(la - lb)
    relative = diff / max(la, lb, 1e-12)
    warning = diff > settings.absolute and relative > settings.threshold
    unit = context.scene.unit_settings

    def fmt(value):
        system = unit.system if unit.system != "NONE" else "METRIC"
        return bpy.utils.units.to_string(system, "LENGTH", value * unit.scale_length,
                                         precision=3)
    from . import floating_simulation as floating
    text = f"A {fmt(la)}   B {fmt(lb)}   Δ {fmt(diff)} ({relative:.1%})"
    size = round(11 * max(.75, context.preferences.system.ui_scale))
    floating._label(blf, text, region_mouse[0] + 18, region_mouse[1] + 18, size,
                    (1, .32, .32, 1) if warning else (.45, .78, 1, 1))


def _draw_editor_pixels():
    context = bpy.context
    op = _active_editor()
    if op is None:
        return
    try:
        view = _view_for_context(context)
        if view is None:
            return
        if context.scene.cloth_next_show_sewing:
            for seam in _editor_seams(op):
                renderer.draw_pixels(context, view, seam, selected=True)
            if op.mode != "EDIT":
                seams = _editor_seams(op)
                if seams and op.mode != "MULTI":
                    mouse = None
                    if op.mouse_region and op.mouse_region[0] == context.region.as_pointer():
                        mouse = op.mouse_region[1]
                    _length_label(context, op, seams[0].first, seams[0].second, mouse)
        from . import tool_overlay as overlay_ui
        layout = overlay_ui.draw(context, op.toolbar_items(), op.hint(), op.toolbar_hover,
                                 warning=op.message_is_error)
        op.layouts[context.region.as_pointer()] = layout
    except Exception:
        _log.exception("Sewing toolbar draw failed")


_draw_handle = None
_pixel_handle = None


def _playback(_scene):
    return bool(getattr(getattr(bpy.context, "screen", None), "is_animation_playing", False))


def _visible_records(scene):
    if not interaction_visible(scene.cloth_next_show_sewing, True, playback=_playback(scene),
                               baking=shared_controller.snapshot().active):
        return ()
    settings = scene.cloth_next_sewing_visual
    selected_index = scene.cloth_next_sewing_index
    definitions = scene.cloth_next_sewing_definitions
    op = _active_editor()
    editing = op is not None and op.mode == "EDIT"
    result = []
    for record in _seam_records(scene):
        item = definitions[record.index]
        if not item.show_overlay:
            continue
        selected = record.index == selected_index
        if not selected and not settings.inactive and not editing:
            continue
        emphasis = 1.0
        if not record.enabled:
            emphasis = .45
        hovered = editing and op.hover_seam == record.index
        result.append((record, selected or hovered, emphasis))
    return result


def _draw_overlay():
    context = bpy.context
    try:
        view = _view_for_context(context)
        if view is None:
            return
        for record, selected, emphasis in _visible_records(context.scene):
            renderer.draw(context, view, record.geometry, selected=selected, emphasis=emphasis)
    except Exception:
        _log.exception("Sewing overlay draw failed")


def _draw_overlay_pixels():
    context = bpy.context
    try:
        view = _view_for_context(context)
        if view is None:
            return
        for record, selected, emphasis in _visible_records(context.scene):
            renderer.draw_pixels(context, view, record.geometry, selected=selected,
                                 emphasis=emphasis)
    except Exception:
        _log.exception("Sewing overlay draw failed")


def _ensure_draw_handler():
    global _draw_handle, _pixel_handle
    if _draw_handle is None and hasattr(bpy.types, "SpaceView3D"):
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_overlay, (), "WINDOW", "POST_VIEW")
        _pixel_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_overlay_pixels, (), "WINDOW", "POST_PIXEL")


_HANDLER_SLOTS = ("undo_pre", "redo_pre", "load_pre")


def register():
    _ensure_draw_handler()
    bpy.types.Scene.cloth_next_sewing_visual = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_sewing_visual)
    validation_state.add_depsgraph_observer(_on_depsgraph)
    handlers = bpy.app.handlers
    for name in _HANDLER_SLOTS:
        rows = getattr(handlers, name, None)
        if rows is not None and _reset_editors not in rows:
            rows.append(_reset_editors)


def unregister():
    global _draw_handle, _pixel_handle
    for op in tuple(_editor_sessions.values()):
        op.finish()
    for handle in (_draw_handle, _pixel_handle):
        if handle is not None:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
    _draw_handle = _pixel_handle = None
    validation_state.remove_depsgraph_observer(_on_depsgraph)
    handlers = bpy.app.handlers
    for name in _HANDLER_SLOTS:
        rows = getattr(handlers, name, None)
        if rows is not None and _reset_editors in rows:
            rows.remove(_reset_editors)
    if hasattr(bpy.types.Scene, "cloth_next_sewing_visual"):
        del bpy.types.Scene.cloth_next_sewing_visual
    viewport_pick.clear()
    _invalidate_records()


CLASSES = (CLOTHNEXT_PG_sewing_vertex, CLOTHNEXT_PG_sewing_pair,
           CLOTHNEXT_PG_sewing_definition, CLOTHNEXT_PG_sewing_visual,
           CLOTHNEXT_OT_remove_sewing, CLOTHNEXT_OT_flip_sewing,
           CLOTHNEXT_OT_select_sewing, CLOTHNEXT_OT_edit_sewing)
