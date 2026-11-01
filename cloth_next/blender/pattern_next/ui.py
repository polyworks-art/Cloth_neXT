# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar panels of the PATTERN NeXt workspace (both of its views).

Panels only poll inside the PATTERN NeXt workspace; other workspaces keep
their sidebars unchanged. The one global entry is "Open PATTERN NeXt" in
Blender's Window menu, the manual restore path for the workspace tab.
"""

from __future__ import annotations

import bpy

from . import model, workspace
from .operators import _active


class _PatternPanel(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "PATTERN NeXt"

    @classmethod
    def poll(cls, context):
        return workspace.is_pattern_workspace(getattr(context, "workspace", None))


class CLOTHNEXT_UL_patterns(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property,
                  index=0, flt_flag=0):
        from .draw import is_selected
        row = layout.row(align=True)
        op = row.operator("clothnext.pattern_select", text=item.name, emboss=False,
                          icon="RADIOBUT_ON" if is_selected(item) else "RADIOBUT_OFF")
        op.identifier = item.identifier
        if item.status:
            row.label(text="", icon="ERROR")
        row.label(text=f"{item.triangle_count} tris")


class CLOTHNEXT_UL_pattern_seams(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property,
                  index=0, flt_flag=0):
        row = layout.row(align=True)
        row.label(text=item.name, icon="ERROR" if item.status else "AUTOMERGE_ON")
        row.prop(item, "flipped", text="", icon="ARROW_LEFTRIGHT", toggle=True)
        op = row.operator("clothnext.pattern_seam_remove", text="", icon="X", emboss=False)
        op.identifier = item.identifier


class CLOTHNEXT_PT_pattern(_PatternPanel):
    bl_label = "Pattern"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        pattern = _active(context)
        if pattern is None:
            layout.label(text="No pattern selected", icon="INFO")
            layout.label(text="Draw one in the 2D editor (H, S, E)")
            return
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(pattern, "name")
        col = layout.column(align=True)
        col.prop(pattern, "particle_distance")
        if pattern.shape_kind in {"RECTANGLE", "ELLIPSE"}:
            col = layout.column(align=True)
            col.prop(pattern, "primitive_size", index=0, text="Width")
            col.prop(pattern, "primitive_size", index=1, text="Height")
        else:
            layout.label(text="Free outline (edit points in the 2D editor)", icon="MESH_DATA")
        col = layout.column(align=True)
        col.prop(pattern, "location", index=0, text="2D X")
        col.prop(pattern, "location", index=1, text="Y")
        col.prop(pattern, "rotation", text="2D Rotation")
        layout.prop(pattern, "material")
        if pattern.status:
            box = layout.box()
            box.alert = True
            box.label(text=pattern.status, icon="ERROR")
        box = layout.box()
        box.use_property_split = False
        grid = box.grid_flow(columns=2, even_columns=True)
        grid.label(text=f"Vertices {pattern.vertex_count}")
        grid.label(text=f"Triangles {pattern.triangle_count}")
        grid.label(text=f"Area {pattern.area * 1e4:.1f} cm²")
        grid.label(text=f"Meshing {pattern.mesh_time * 1000:.0f} ms")
        obj = pattern.mesh_object
        if obj is not None:
            row = box.row()
            row.label(text=f"Cloth NeXt panel: {obj.name}", icon="MOD_CLOTH")
        else:
            box.label(text="No 3D panel (rebuild to recreate)", icon="ERROR")
        row = layout.row(align=True)
        row.operator("clothnext.pattern_duplicate", text="Duplicate", icon="DUPLICATE")
        row.operator("clothnext.pattern_mirror", text="Mirror", icon="MOD_MIRROR")
        row.operator("clothnext.pattern_delete", text="", icon="TRASH")
        row = layout.row(align=True)
        row.operator("clothnext.pattern_rebuild", text="Rebuild", icon="FILE_REFRESH")
        if pattern.symmetry_partner:
            partner = model.find(context.scene, pattern.symmetry_partner)
            row = layout.row(align=True)
            row.prop(pattern, "symmetry_linked",
                     text=f"Linked to {partner.name if partner else '?'}")
            row.operator("clothnext.pattern_unlink_symmetry", text="", icon="UNLINKED")
        internal = [c for c in pattern.contours]
        if internal:
            col = layout.column(align=True)
            col.label(text="Internal Shapes")
            for contour in internal:
                row = col.row(align=True)
                row.label(text=f"{'Hole' if contour.kind == 'HOLE' else 'Line'} "
                               f"({len(contour.points)} points)",
                          icon="MOD_BOOLEAN" if contour.kind == "HOLE" else "IPO_LINEAR")
                op = row.operator("clothnext.pattern_remove_contour", text="", icon="X")
                op.pattern, op.uid = pattern.identifier, contour.uid


class CLOTHNEXT_PT_pattern_list(_PatternPanel):
    bl_label = "Patterns"
    bl_order = 1

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        layout.template_list("CLOTHNEXT_UL_patterns", "", scene, "cloth_next_patterns",
                             scene.cloth_next_pattern_settings, "pattern_index", rows=4)
        row = layout.row(align=True)
        op = row.operator("clothnext.pattern_add_primitive", text="Rectangle", icon="MESH_PLANE")
        op.kind = "RECTANGLE"
        op = row.operator("clothnext.pattern_add_primitive", text="Ellipse", icon="MESH_CIRCLE")
        op.kind = "ELLIPSE"
        layout.prop(scene.cloth_next_pattern_settings, "default_particle_distance")


class CLOTHNEXT_PT_pattern_sewing(_PatternPanel):
    bl_label = "Sewing"
    bl_order = 2

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        layout.template_list("CLOTHNEXT_UL_pattern_seams", "", scene, "cloth_next_pattern_seams",
                             scene.cloth_next_pattern_settings, "seam_index", rows=3)
        index = scene.cloth_next_pattern_settings.seam_index
        seams = scene.cloth_next_pattern_seams
        if 0 <= index < len(seams) and seams[index].status:
            box = layout.box()
            box.alert = True
            box.label(text=seams[index].status, icon="ERROR")
        col = layout.column(align=True)
        col.label(text="Seams become Cloth NeXt Sewing definitions", icon="INFO")
        col.label(text="and are re-derived after every remesh.")
        col.label(text="Simulating them with AETHER follows next.")


class CLOTHNEXT_PT_pattern_arrangement(_PatternPanel):
    bl_label = "Arrangement"
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cloth_next_pattern_settings
        layout.label(text="Move panels around the avatar in the 3D view;")
        layout.label(text="their 2D shape never changes.")
        row = layout.row(align=True)
        op = row.operator("clothnext.pattern_place", text="Front", icon="AXIS_FRONT")
        op.side = "FRONT"
        op = row.operator("clothnext.pattern_place", text="Back", icon="AXIS_FRONT")
        op.side = "BACK"
        layout.operator("clothnext.pattern_restage", icon="LOOP_BACK")
        layout.prop(settings, "staging_offset")


class CLOTHNEXT_PT_pattern_display(_PatternPanel):
    bl_label = "Display & Snapping"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        settings = context.scene.cloth_next_pattern_settings
        layout = self.layout
        row = layout.row(align=True)
        row.prop(settings, "snap_grid", toggle=True)
        row.prop(settings, "snap_vertex", toggle=True)
        row.prop(settings, "snap_edge", toggle=True)
        layout.prop(settings, "grid_step")
        row = layout.row(align=True)
        row.prop(settings, "show_mesh", toggle=True)
        row.prop(settings, "show_lengths", toggle=True)
        row.prop(settings, "show_seams", toggle=True)


def _window_menu(self, context):
    self.layout.separator()
    self.layout.operator("clothnext.pattern_open_workspace", icon="WORKSPACE")


def register():
    bpy.types.TOPBAR_MT_window.append(_window_menu)


def unregister():
    bpy.types.TOPBAR_MT_window.remove(_window_menu)


CLASSES = (CLOTHNEXT_UL_patterns, CLOTHNEXT_UL_pattern_seams, CLOTHNEXT_PT_pattern,
           CLOTHNEXT_PT_pattern_list, CLOTHNEXT_PT_pattern_sewing,
           CLOTHNEXT_PT_pattern_arrangement, CLOTHNEXT_PT_pattern_display)
