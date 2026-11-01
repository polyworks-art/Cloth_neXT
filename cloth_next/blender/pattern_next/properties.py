# SPDX-License-Identifier: GPL-3.0-or-later
"""Persistent PATTERN NeXt data (Scene-level RNA, saved in the .blend).

The parametric pattern is the source of truth; the generated mesh object is
derived data referenced by ``mesh_object``. Schema changes bump
``SCHEMA_VERSION`` in ``cloth_next.pattern_next`` and add a step to
``model.migrate`` (forward-only, applied on load).
"""

from __future__ import annotations

import bpy

from ...pattern_next import SCHEMA_VERSION

DEFAULT_PARTICLE_DISTANCE = 0.02  # 20 mm, a typical garment default

TOOLS = (
    ("SELECT", "Select / Move", "Select, move and rotate patterns (Q)", "RESTRICT_SELECT_OFF", 0),
    ("POLYGON", "Polygon", "Click points; click the first point or press Enter to close (H)",
     "MESH_DATA", 1),
    ("RECTANGLE", "Rectangle", "Drag a rectangle; click for exact width and height (S)",
     "MESH_PLANE", 2),
    ("ELLIPSE", "Ellipse", "Drag an ellipse; click for exact size (E)", "MESH_CIRCLE", 3),
    ("EDIT", "Edit Pattern", "Move points and edges; double-click an edge to add a point (Z)",
     "VERTEXSEL", 4),
    ("CURVE", "Edit Curve", "Drag an edge to bend it; drag handles to shape curves (V)",
     "CURVE_BEZCURVE", 5),
    ("INTERNAL", "Internal Shape", "Draw holes and internal lines inside a pattern (G)",
     "MOD_BOOLEAN", 6),
    ("SEWING", "Sewing", "Click two edges to sew them (N)", "AUTOMERGE_OFF", 7),
)


def _redraw(_self=None, context=None):
    from . import draw
    draw.tag_redraw()


def _resync(self, context):
    from . import sync
    sync.request(self)


def _placement(self, context):
    from . import sync
    sync.placement_changed(self)


def _primitive(self, context):
    from . import model
    model.apply_primitive(self)
    from . import sync
    sync.request(self)


def _material(self, context):
    from . import sync
    sync.assign_material(self)


def _renamed(self, context):
    from . import sync
    sync.rename_object(self)


def _seam_changed(self, context):
    from . import sync
    sync.request_seam(self)


class CLOTHNEXT_PG_pattern_point(bpy.types.PropertyGroup):
    uid: bpy.props.IntProperty(default=0, min=0)
    co: bpy.props.FloatVectorProperty(size=2, subtype="TRANSLATION", unit="LENGTH")
    handle_in: bpy.props.FloatVectorProperty(size=2, subtype="TRANSLATION", unit="LENGTH")
    handle_out: bpy.props.FloatVectorProperty(size=2, subtype="TRANSLATION", unit="LENGTH")
    curve_out: bpy.props.BoolProperty(default=False)
    smooth: bpy.props.BoolProperty(default=False)


class CLOTHNEXT_PG_pattern_contour(bpy.types.PropertyGroup):
    uid: bpy.props.IntProperty(default=0, min=0)
    kind: bpy.props.EnumProperty(items=(("HOLE", "Hole", "Cut-out inside the pattern"),
                                        ("LINE", "Internal Line", "Fold or detail line embedded "
                                         "in the mesh")), default="HOLE")
    points: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_pattern_point)


class CLOTHNEXT_PG_pattern(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(default="", options={"HIDDEN"})
    name: bpy.props.StringProperty(name="Name", default="Pattern", update=_renamed)
    schema_version: bpy.props.IntProperty(default=SCHEMA_VERSION, options={"HIDDEN"})
    revision: bpy.props.IntProperty(default=0, options={"HIDDEN"})
    shape_kind: bpy.props.EnumProperty(
        name="Shape", default="POLYGON",
        items=(("POLYGON", "Polygon", "Free outline of straight and curved segments"),
               ("RECTANGLE", "Rectangle", "Parametric rectangle (width and height)"),
               ("ELLIPSE", "Ellipse", "Parametric ellipse (width and height)")))
    primitive_size: bpy.props.FloatVectorProperty(
        name="Size", size=2, subtype="XYZ", unit="LENGTH", default=(0.4, 0.3), min=0.001,
        description="Width and height of the parametric rectangle or ellipse",
        update=_primitive)
    points: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_pattern_point)
    contours: bpy.props.CollectionProperty(type=CLOTHNEXT_PG_pattern_contour)
    next_uid: bpy.props.IntProperty(default=1, min=1, options={"HIDDEN"})
    location: bpy.props.FloatVectorProperty(
        name="2D Position", size=2, subtype="XYZ", unit="LENGTH", update=_placement,
        description="Position on the 2D pattern canvas (does not move the 3D panel)")
    rotation: bpy.props.FloatProperty(
        name="2D Rotation", subtype="ANGLE", update=_placement,
        description="Rotation on the 2D pattern canvas (grain direction for UVs)")
    particle_distance: bpy.props.FloatProperty(
        name="Particle Distance", default=DEFAULT_PARTICLE_DISTANCE, min=0.002, max=0.5,
        soft_min=0.003, soft_max=0.1, subtype="DISTANCE", unit="LENGTH", update=_resync,
        description="Approximate triangle edge length of the generated cloth mesh. "
                    "Smaller values give a denser, more detailed simulation mesh")
    mesh_object: bpy.props.PointerProperty(type=bpy.types.Object, options={"HIDDEN"})
    material: bpy.props.PointerProperty(
        name="Material", type=bpy.types.Material, update=_material,
        description="Render material of the generated panel")
    symmetry_partner: bpy.props.StringProperty(default="", options={"HIDDEN"})
    symmetry_linked: bpy.props.BoolProperty(
        name="Linked Symmetry", default=False,
        description="Point edits on this pattern are mirrored onto its partner")
    status: bpy.props.StringProperty(default="")
    vertex_count: bpy.props.IntProperty(default=0)
    triangle_count: bpy.props.IntProperty(default=0)
    area: bpy.props.FloatProperty(default=0.0, unit="AREA")
    mesh_time: bpy.props.FloatProperty(default=0.0)


class CLOTHNEXT_PG_pattern_seam(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty(default="", options={"HIDDEN"})
    name: bpy.props.StringProperty(default="Seam")
    a_pattern: bpy.props.StringProperty(default="")
    a_start: bpy.props.IntProperty(default=0)
    a_end: bpy.props.IntProperty(default=0)
    b_pattern: bpy.props.StringProperty(default="")
    b_start: bpy.props.IntProperty(default=0)
    b_end: bpy.props.IntProperty(default=0)
    flipped: bpy.props.BoolProperty(
        name="Crossed", default=False, update=_seam_changed,
        description="Pair the start of side A with the end of side B")
    sewing_identifier: bpy.props.StringProperty(default="", options={"HIDDEN"})
    status: bpy.props.StringProperty(default="")


class CLOTHNEXT_PG_pattern_settings(bpy.types.PropertyGroup):
    schema_version: bpy.props.IntProperty(default=SCHEMA_VERSION, options={"HIDDEN"})
    default_particle_distance: bpy.props.FloatProperty(
        name="New Pattern Particle Distance", default=DEFAULT_PARTICLE_DISTANCE, min=0.002,
        max=0.5, subtype="DISTANCE", unit="LENGTH")
    snap_grid: bpy.props.BoolProperty(name="Grid", default=True, update=_redraw,
                                      description="Snap to the measurement grid")
    snap_vertex: bpy.props.BoolProperty(name="Points", default=True, update=_redraw,
                                        description="Snap to pattern points")
    snap_edge: bpy.props.BoolProperty(name="Edges", default=True, update=_redraw,
                                      description="Snap to pattern edges")
    grid_step: bpy.props.FloatProperty(
        name="Grid Step", default=0.0, min=0.0, subtype="DISTANCE", unit="LENGTH",
        update=_redraw, description="Snapping grid step; 0 follows the zoom (1/2/5 steps)")
    show_mesh: bpy.props.BoolProperty(name="Mesh", default=True, update=_redraw,
                                      description="Show the generated triangulation")
    show_lengths: bpy.props.BoolProperty(name="Lengths", default=True, update=_redraw,
                                         description="Show segment lengths")
    show_seams: bpy.props.BoolProperty(name="Seams", default=True, update=_redraw,
                                       description="Show sewing connections")
    staging_offset: bpy.props.FloatProperty(
        name="Staging Depth", default=-0.5, subtype="DISTANCE", unit="LENGTH",
        description="Y position of new 3D panels (their staging area in front of the origin)")
    seam_index: bpy.props.IntProperty(default=0, min=0)
    pattern_index: bpy.props.IntProperty(default=0, min=0)


class CLOTHNEXT_PG_pattern_ui(bpy.types.PropertyGroup):
    """Window-manager state: the active tool is UI state, not scene data."""
    tool: bpy.props.EnumProperty(name="Tool", items=TOOLS, default="POLYGON", update=_redraw)
    internal_kind: bpy.props.EnumProperty(
        name="Internal", default="HOLE", update=_redraw,
        items=(("HOLE", "Hole", "Closed polygon cut out of the pattern"),
               ("LINE", "Line", "Open internal line embedded in the mesh")))


CLASSES = (CLOTHNEXT_PG_pattern_point, CLOTHNEXT_PG_pattern_contour,
           CLOTHNEXT_PG_pattern, CLOTHNEXT_PG_pattern_seam,
           CLOTHNEXT_PG_pattern_settings, CLOTHNEXT_PG_pattern_ui)


def attach():
    bpy.types.Scene.cloth_next_patterns = bpy.props.CollectionProperty(
        type=CLOTHNEXT_PG_pattern)
    bpy.types.Scene.cloth_next_pattern_seams = bpy.props.CollectionProperty(
        type=CLOTHNEXT_PG_pattern_seam)
    bpy.types.Scene.cloth_next_pattern_settings = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_pattern_settings)
    bpy.types.WindowManager.cloth_next_pattern_ui = bpy.props.PointerProperty(
        type=CLOTHNEXT_PG_pattern_ui)


def detach():
    for owner, name in ((bpy.types.WindowManager, "cloth_next_pattern_ui"),
                        (bpy.types.Scene, "cloth_next_pattern_settings"),
                        (bpy.types.Scene, "cloth_next_pattern_seams"),
                        (bpy.types.Scene, "cloth_next_patterns")):
        if hasattr(owner, name):
            delattr(owner, name)
