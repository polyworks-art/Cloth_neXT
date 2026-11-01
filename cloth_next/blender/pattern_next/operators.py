# SPDX-License-Identifier: GPL-3.0-or-later
"""Undoable PATTERN NeXt operations used by the editor, menus and panels."""

from __future__ import annotations

from dataclasses import replace
import uuid

import bpy

from ...pattern_next import editing
from ...pattern_next.curves import segment_length
from ...pattern_next.shapes import Contour, mirror_points
from ...pattern_next.validation import PatternError
from . import draw, model, sync

_clipboard = []


def _active(context):
    scene = context.scene
    obj = context.view_layer.objects.active
    pattern = model.for_object(scene, obj)
    if pattern is None:
        chosen = [p for p in scene.cloth_next_patterns if draw.is_selected(p)]
        pattern = chosen[0] if chosen else None
    return pattern


def _selected(context):
    return [p for p in context.scene.cloth_next_patterns if draw.is_selected(p)]


def snapshot(pattern):
    return {
        "name": pattern.name, "shape_kind": pattern.shape_kind,
        "primitive_size": tuple(pattern.primitive_size),
        "points": model.outline(pattern),
        "contours": [(c.kind, c.uid, model.read_points(c.points)) for c in pattern.contours],
        "next_uid": pattern.next_uid, "location": tuple(pattern.location),
        "rotation": float(pattern.rotation), "particle_distance": float(pattern.particle_distance),
        "material": pattern.material,
    }


def create_from(context, data, location, name=None, select=True):
    scene = context.scene
    name = model.unique_name(scene, name or data["name"])
    pattern = scene.cloth_next_patterns.add()
    pattern["_pn_suspend"] = 1
    pattern.identifier = uuid.uuid4().hex
    pattern.name = name
    pattern.shape_kind = data["shape_kind"]
    pattern.primitive_size = data["primitive_size"]
    model.write_points(pattern.points, data["points"])
    for kind, uid, points in data["contours"]:
        item = pattern.contours.add()
        item.kind, item.uid = kind, uid
        model.write_points(item.points, points)
    pattern.next_uid = data["next_uid"]
    pattern.location = location
    pattern.rotation = data["rotation"]
    pattern.particle_distance = data["particle_distance"]
    pattern.material = data["material"]
    del pattern["_pn_suspend"]
    try:
        sync.rebuild(scene, pattern, force=True)
    except PatternError:
        scene.cloth_next_patterns.remove(model.index_of(scene, pattern))
        raise
    if select:
        from .editor import select_pattern
        select_pattern(context, pattern, extend=True)
    return pattern


def _mirror_contours(contours):
    result = []
    for kind, uid, points in contours:
        if kind == "HOLE":
            result.append((kind, uid, mirror_points(points)))
        else:
            result.append((kind, uid, tuple(replace(
                p, co=(-p.co[0], p.co[1]), handle_in=(-p.handle_in[0], p.handle_in[1]),
                handle_out=(-p.handle_out[0], p.handle_out[1])) for p in points)))
    return result


def sync_symmetry(scene, pattern):
    """Linked symmetry: the partner is regenerated as the mirror image.

    Point uids are preserved by ``mirror_points``, so seams on the partner
    survive. The partner keeps its own 2D placement and 3D arrangement.
    """
    if not pattern.symmetry_linked:
        return
    partner = model.find(scene, pattern.symmetry_partner)
    if partner is None:
        return
    contours = _mirror_contours([(c.kind, c.uid, model.read_points(c.points))
                                 for c in pattern.contours])
    model.make_free_form(partner)
    ok, message = sync.commit(scene, partner, mirror_points(model.outline(pattern)), contours)
    if not ok:
        partner.status = f"Symmetry: {message}"


class _PatternOperator(bpy.types.Operator):
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and hasattr(context.scene, "cloth_next_patterns")


class CLOTHNEXT_OT_pattern_delete(_PatternOperator):
    """Delete the selected patterns and their generated panels"""
    bl_idname = "clothnext.pattern_delete"
    bl_label = "Delete Pattern"

    def execute(self, context):
        chosen = [p.identifier for p in _selected(context)]
        if not chosen:
            return {"CANCELLED"}
        for identifier in chosen:
            pattern = model.find(context.scene, identifier)
            if pattern is not None:
                sync.remove_pattern(context.scene, pattern)
        draw.tag_redraw()
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_duplicate(_PatternOperator):
    """Duplicate the selected patterns (new panels, same shape)"""
    bl_idname = "clothnext.pattern_duplicate"
    bl_label = "Duplicate Pattern"

    def execute(self, context):
        chosen = [snapshot(p) for p in _selected(context)]
        if not chosen:
            return {"CANCELLED"}
        from .editor import select_pattern
        select_pattern(context, None)
        for data in chosen:
            x, y = data["location"]
            create_from(context, data, (x + 0.05, y - 0.05))
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_copy(_PatternOperator):
    """Copy the selected patterns"""
    bl_idname = "clothnext.pattern_copy"
    bl_label = "Copy Pattern"
    bl_options = {"REGISTER", "INTERNAL"}

    def execute(self, context):
        chosen = [snapshot(p) for p in _selected(context)]
        if not chosen:
            return {"CANCELLED"}
        _clipboard[:] = chosen
        self.report({"INFO"}, f"Copied {len(chosen)} pattern(s)")
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_paste(_PatternOperator):
    """Paste copied patterns at the mouse position"""
    bl_idname = "clothnext.pattern_paste"
    bl_label = "Paste Pattern"
    x: bpy.props.FloatProperty(options={"HIDDEN"})
    y: bpy.props.FloatProperty(options={"HIDDEN"})

    def execute(self, context):
        if not _clipboard:
            return {"CANCELLED"}
        from .editor import select_pattern
        select_pattern(context, None)
        cx = sum(d["location"][0] for d in _clipboard) / len(_clipboard)
        cy = sum(d["location"][1] for d in _clipboard) / len(_clipboard)
        for data in _clipboard:
            x, y = data["location"]
            create_from(context, data, (self.x + x - cx, self.y + y - cy))
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_mirror(_PatternOperator):
    """Create a mirrored copy; Linked keeps both halves symmetric while editing"""
    bl_idname = "clothnext.pattern_mirror"
    bl_label = "Mirror Pattern"
    linked: bpy.props.BoolProperty(name="Linked Symmetry", default=True)

    def execute(self, context):
        sources = [p.identifier for p in _selected(context)]
        if not sources:
            return {"CANCELLED"}
        from .editor import select_pattern
        made = []
        for identifier in sources:
            # Resolve by identifier: adding patterns reallocates the collection.
            source = model.find(context.scene, identifier)
            data = snapshot(source)
            data["points"] = mirror_points(data["points"])
            data["contours"] = _mirror_contours(data["contours"])
            data["rotation"] = -data["rotation"]
            ring = model.canvas_outline(source, 0.002)
            width = max(p[0] for p in ring) - min(p[0] for p in ring)
            x, y = data["location"]
            partner = create_from(context, data, (x + width + 0.05, y),
                                  name=data["name"] + " (mirror)", select=False)
            source = model.find(context.scene, identifier)
            if self.linked:
                source.symmetry_partner, partner.symmetry_partner = partner.identifier, source.identifier
                source.symmetry_linked = partner.symmetry_linked = True
            made.append(partner.identifier)
        select_pattern(context, None)
        for identifier in made:
            select_pattern(context, model.find(context.scene, identifier), extend=True)
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_unlink_symmetry(_PatternOperator):
    """Stop mirroring edits between this pattern and its partner"""
    bl_idname = "clothnext.pattern_unlink_symmetry"
    bl_label = "Unlink Symmetry"

    def execute(self, context):
        pattern = _active(context)
        if pattern is None:
            return {"CANCELLED"}
        partner = model.find(context.scene, pattern.symmetry_partner)
        for item in (pattern, partner):
            if item is not None:
                item.symmetry_linked = False
                item.symmetry_partner = ""
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_add_primitive(_PatternOperator):
    """Create a rectangle or ellipse with exact dimensions"""
    bl_idname = "clothnext.pattern_add_primitive"
    bl_label = "Add Pattern"
    kind: bpy.props.EnumProperty(items=(("RECTANGLE", "Rectangle", ""),
                                        ("ELLIPSE", "Ellipse", "")), default="RECTANGLE")
    x: bpy.props.FloatProperty(name="X", subtype="DISTANCE", unit="LENGTH")
    y: bpy.props.FloatProperty(name="Y", subtype="DISTANCE", unit="LENGTH")
    width: bpy.props.FloatProperty(name="Width", default=0.4, min=0.005, subtype="DISTANCE",
                                   unit="LENGTH")
    height: bpy.props.FloatProperty(name="Height", default=0.3, min=0.005, subtype="DISTANCE",
                                    unit="LENGTH")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "width")
        layout.prop(self, "height")

    def execute(self, context):
        base = model.rectangle(self.width, self.height) if self.kind == "RECTANGLE" else \
            model.ellipse(self.width, self.height)
        points = model.translate(base, (self.x, self.y))
        scene = context.scene
        pattern = model.new_pattern(scene, self.kind.title(), points, self.kind,
                                    (self.width, self.height))
        try:
            sync.rebuild(scene, pattern, force=True)
        except PatternError as exc:
            scene.cloth_next_patterns.remove(model.index_of(scene, pattern))
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        from .editor import select_pattern
        select_pattern(context, pattern)
        return {"FINISHED"}


def _target(context, identifier):
    return model.find(context.scene, identifier)


class CLOTHNEXT_OT_pattern_segment(_PatternOperator):
    """Change one outline segment"""
    bl_idname = "clothnext.pattern_segment"
    bl_label = "Edit Segment"
    pattern: bpy.props.StringProperty(options={"HIDDEN"})
    segment: bpy.props.IntProperty(options={"HIDDEN"})
    t: bpy.props.FloatProperty(default=0.5, options={"HIDDEN"})
    action: bpy.props.EnumProperty(items=(("CURVE", "Make Curved", ""),
                                          ("STRAIGHT", "Make Straight", ""),
                                          ("SPLIT", "Add Point", "")))

    def execute(self, context):
        pattern = _target(context, self.pattern)
        if pattern is None:
            return {"CANCELLED"}
        points = model.outline(pattern)
        if self.action == "CURVE":
            new = editing.make_curved(points, self.segment, bulge=0.15)
        elif self.action == "STRAIGHT":
            new = editing.make_straight(points, self.segment)
        else:
            new = editing.insert_point(points, self.segment, self.t, model.allocate(pattern)[0])
        model.make_free_form(pattern)
        ok, message = sync.commit(context.scene, pattern, new)
        if not ok:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        sync_symmetry(context.scene, pattern)
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_set_length(_PatternOperator):
    """Set the exact length of an outline segment (its end point moves)"""
    bl_idname = "clothnext.pattern_set_length"
    bl_label = "Set Edge Length"
    pattern: bpy.props.StringProperty(options={"HIDDEN"})
    segment: bpy.props.IntProperty(options={"HIDDEN"})
    length: bpy.props.FloatProperty(name="Length", min=0.001, subtype="DISTANCE", unit="LENGTH")

    def invoke(self, context, event):
        pattern = _target(context, self.pattern)
        if pattern is None:
            return {"CANCELLED"}
        self.length = segment_length(Contour(model.outline(pattern)), self.segment)
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        pattern = _target(context, self.pattern)
        if pattern is None:
            return {"CANCELLED"}
        try:
            new = editing.set_segment_length(model.outline(pattern), self.segment, self.length)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        model.make_free_form(pattern)
        ok, message = sync.commit(context.scene, pattern, new)
        if not ok:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        sync_symmetry(context.scene, pattern)
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_remove_point(_PatternOperator):
    """Remove an outline point"""
    bl_idname = "clothnext.pattern_remove_point"
    bl_label = "Remove Point"
    pattern: bpy.props.StringProperty(options={"HIDDEN"})
    point: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        pattern = _target(context, self.pattern)
        if pattern is None:
            return {"CANCELLED"}
        try:
            new = editing.remove_point(model.outline(pattern), self.point)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        model.make_free_form(pattern)
        ok, message = sync.commit(context.scene, pattern, new)
        if not ok:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        sync_symmetry(context.scene, pattern)
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_remove_contour(_PatternOperator):
    """Remove a hole or internal line"""
    bl_idname = "clothnext.pattern_remove_contour"
    bl_label = "Remove Internal Shape"
    pattern: bpy.props.StringProperty(options={"HIDDEN"})
    uid: bpy.props.IntProperty(options={"HIDDEN"})

    def execute(self, context):
        pattern = _target(context, self.pattern)
        if pattern is None:
            return {"CANCELLED"}
        contours = [(c.kind, c.uid, model.read_points(c.points)) for c in pattern.contours
                    if c.uid != self.uid]
        ok, message = sync.commit(context.scene, pattern, contours=contours)
        if not ok:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_rebuild(_PatternOperator):
    """Regenerate the panels from their pattern data"""
    bl_idname = "clothnext.pattern_rebuild"
    bl_label = "Rebuild Panels"
    all_patterns: bpy.props.BoolProperty(default=False, options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        chosen = list(scene.cloth_next_patterns) if self.all_patterns else _selected(context)
        failed = 0
        for pattern in chosen:
            try:
                sync.rebuild(scene, pattern, force=True)
            except PatternError as exc:
                pattern.status = str(exc)
                failed += 1
        if failed:
            self.report({"WARNING"}, f"{failed} pattern(s) could not be meshed")
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_select(_PatternOperator):
    """Select this pattern in both editors"""
    bl_idname = "clothnext.pattern_select"
    bl_label = "Select Pattern"
    identifier: bpy.props.StringProperty(options={"HIDDEN"})
    extend: bpy.props.BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        from .editor import select_pattern
        pattern = _target(context, self.identifier)
        if pattern is None:
            return {"CANCELLED"}
        if pattern.mesh_object is None:
            sync.ensure_all(context.scene)
        select_pattern(context, pattern, toggle=self.extend)
        draw.tag_redraw()
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_restage(_PatternOperator):
    """Arrangement: put the selected panels back in the staging area, laid out like
    the 2D canvas and facing the front"""
    bl_idname = "clothnext.pattern_restage"
    bl_label = "Reset Arrangement"

    def execute(self, context):
        scene = context.scene
        depth = scene.cloth_next_pattern_settings.staging_offset
        chosen = _selected(context) or list(scene.cloth_next_patterns)
        for pattern in chosen:
            obj = pattern.mesh_object
            if obj is None:
                continue
            obj.location = (pattern.location[0], depth, pattern.location[1])
            obj.rotation_euler = (0.0, 0.0, 0.0)
            obj.scale = (1.0, 1.0, 1.0)
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_place(_PatternOperator):
    """Arrangement: move the selected panels in front of or behind the body"""
    bl_idname = "clothnext.pattern_place"
    bl_label = "Place Panels"
    side: bpy.props.EnumProperty(items=(("FRONT", "Front", "In front of the origin (−Y)"),
                                        ("BACK", "Back", "Behind the origin (+Y), turned around")))
    distance: bpy.props.FloatProperty(name="Distance", default=0.25, min=0.0, subtype="DISTANCE",
                                      unit="LENGTH")

    def execute(self, context):
        import math
        for pattern in _selected(context):
            obj = pattern.mesh_object
            if obj is None:
                continue
            front = self.side == "FRONT"
            obj.location.y = -self.distance if front else self.distance
            obj.rotation_euler = (0.0, 0.0, 0.0 if front else math.pi)
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_seam_add(_PatternOperator):
    """Sew two pattern edges"""
    bl_idname = "clothnext.pattern_seam_add"
    bl_label = "Add Seam"
    a_pattern: bpy.props.StringProperty(options={"HIDDEN"})
    a_start: bpy.props.IntProperty(options={"HIDDEN"})
    a_end: bpy.props.IntProperty(options={"HIDDEN"})
    b_pattern: bpy.props.StringProperty(options={"HIDDEN"})
    b_start: bpy.props.IntProperty(options={"HIDDEN"})
    b_end: bpy.props.IntProperty(options={"HIDDEN"})
    flipped: bpy.props.BoolProperty(options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        pa, pb = _target(context, self.a_pattern), _target(context, self.b_pattern)
        if pa is None or pb is None:
            return {"CANCELLED"}
        seam = scene.cloth_next_pattern_seams.add()
        seam.identifier = uuid.uuid4().hex
        seam.name = f"{pa.name} ↔ {pb.name}"
        seam.a_pattern, seam.a_start, seam.a_end = pa.identifier, self.a_start, self.a_end
        seam.b_pattern, seam.b_start, seam.b_end = pb.identifier, self.b_start, self.b_end
        seam["_pn_suspend"] = 1
        seam.flipped = self.flipped
        del seam["_pn_suspend"]
        if not sync.derive_seam(scene, seam):
            self.report({"WARNING"}, f"Seam saved, but not sewable yet: {seam.status}")
        scene.cloth_next_pattern_settings.seam_index = len(scene.cloth_next_pattern_seams) - 1
        draw.tag_redraw()
        return {"FINISHED"}


class CLOTHNEXT_OT_pattern_seam_remove(_PatternOperator):
    """Delete a seam"""
    bl_idname = "clothnext.pattern_seam_remove"
    bl_label = "Delete Seam"
    identifier: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        for index, seam in enumerate(scene.cloth_next_pattern_seams):
            if seam.identifier == self.identifier:
                sync.remove_seam(scene, index)
                draw.tag_redraw()
                return {"FINISHED"}
        return {"CANCELLED"}


class CLOTHNEXT_MT_pattern_context(bpy.types.Menu):
    bl_label = "Pattern"
    bl_idname = "CLOTHNEXT_MT_pattern_context"

    def draw(self, context):
        from .editor import _context_target as target
        layout = self.layout
        identifier = target.get("pattern")
        if "segment" in target:
            pattern = _target(context, identifier)
            curved = False
            if pattern is not None:
                points = model.outline(pattern)
                curved = points[target["segment"] % len(points)].curve_out
            for action, label, icon in (("SPLIT", "Add Point Here", "ADD"),
                                        ("STRAIGHT" if curved else "CURVE",
                                         "Make Straight" if curved else "Make Curved",
                                         "IPO_LINEAR" if curved else "IPO_BEZIER")):
                op = layout.operator("clothnext.pattern_segment", text=label, icon=icon)
                op.pattern, op.segment, op.action = identifier, target["segment"], action
                op.t = target.get("t", 0.5)
            op = layout.operator("clothnext.pattern_set_length", text="Set Edge Length…",
                                 icon="DRIVER_DISTANCE")
            op.pattern, op.segment = identifier, target["segment"]
            layout.separator()
        if "point" in target:
            op = layout.operator("clothnext.pattern_remove_point", text="Remove Point",
                                 icon="REMOVE")
            op.pattern, op.point = identifier, target["point"]
            layout.separator()
        if "seam" in target:
            op = layout.operator("clothnext.pattern_seam_remove", text="Delete Seam", icon="X")
            op.identifier = target["seam"]
            layout.separator()
        layout.operator("clothnext.pattern_duplicate", icon="DUPLICATE")
        layout.operator("clothnext.pattern_mirror", icon="MOD_MIRROR")
        layout.operator("clothnext.pattern_copy", icon="COPYDOWN")
        layout.separator()
        layout.operator("clothnext.pattern_delete", icon="TRASH")


CLASSES = (CLOTHNEXT_OT_pattern_delete, CLOTHNEXT_OT_pattern_duplicate,
           CLOTHNEXT_OT_pattern_copy, CLOTHNEXT_OT_pattern_paste,
           CLOTHNEXT_OT_pattern_mirror, CLOTHNEXT_OT_pattern_unlink_symmetry,
           CLOTHNEXT_OT_pattern_add_primitive, CLOTHNEXT_OT_pattern_segment,
           CLOTHNEXT_OT_pattern_set_length, CLOTHNEXT_OT_pattern_remove_point,
           CLOTHNEXT_OT_pattern_remove_contour, CLOTHNEXT_OT_pattern_rebuild,
           CLOTHNEXT_OT_pattern_select, CLOTHNEXT_OT_pattern_restage,
           CLOTHNEXT_OT_pattern_place, CLOTHNEXT_OT_pattern_seam_add,
           CLOTHNEXT_OT_pattern_seam_remove, CLOTHNEXT_MT_pattern_context)
