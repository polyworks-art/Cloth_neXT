# SPDX-License-Identifier: GPL-3.0-or-later
"""PATTERN NeXt popovers, menus and icon previews (native Blender UI).

The inspector is the docked column of the 2D editor (``dock.py``), not the
N sidebar: PATTERN NeXt registers no sidebar panels. Native pieces here:
the Display popover (folded toolbar group), the value popover used by the
inspector (in ``dock.py``), and an "Arrange" menu in the header of the
workspace's 3D view. Other workspaces are untouched; the one global entry
is "Open PATTERN NeXt" in the Window menu (the manual restore path for the
workspace tab).
"""

from __future__ import annotations

from pathlib import Path

import bpy

from . import dock, workspace

_previews = {"collection": None}
_ICON_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons" / "pattern_next"
_ICON_NAMES = ("select", "polygon", "rectangle", "ellipse", "curve", "edit_pattern",
               "edit_curve", "internal", "sewing", "mirror", "duplicate", "grid", "snap",
               "measure", "mesh", "seams", "frame", "arrange", "pattern", "flip")


def icon(name):
    collection = _previews["collection"]
    if collection is None or name not in collection:
        return 0
    return collection[name].icon_id


def _icon_kwargs(name, fallback="NONE"):
    value = icon(name)
    return {"icon_value": value} if value else {"icon": fallback}


def _draw_arrange(layout, context):
    row = layout.row(align=True)
    op = row.operator("clothnext.pattern_place", text="Front", icon="AXIS_FRONT")
    op.side = "FRONT"
    op = row.operator("clothnext.pattern_place", text="Back", icon="AXIS_FRONT")
    op.side = "BACK"
    layout.operator("clothnext.pattern_restage", text="Reset to Staging", icon="LOOP_BACK")


def _draw_display(layout, context):
    settings = context.scene.cloth_next_pattern_settings
    col = layout.column(align=True)
    col.prop(settings, "show_grid", **_icon_kwargs("grid"))
    col.prop(settings, "show_lengths", **_icon_kwargs("measure"))
    col.prop(settings, "show_mesh", **_icon_kwargs("mesh"))
    col.prop(settings, "show_seams", **_icon_kwargs("seams"))
    col.prop(settings, "show_names", icon="SORTALPHA")
    layout.separator(factor=0.5)
    col = layout.column(align=True)
    col.prop(settings, "use_snap", **_icon_kwargs("snap"))
    row = col.row(align=True)
    row.active = settings.use_snap
    row.prop(settings, "snap_grid", toggle=True)
    row.prop(settings, "snap_vertex", toggle=True)
    row.prop(settings, "snap_edge", toggle=True)
    col.prop(settings, "grid_step", text="Grid Step (0 = auto)")


class CLOTHNEXT_PT_pattern_display_popover(bpy.types.Panel):
    """Opened from the folded Display button of a narrow 2D editor."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = "Display"
    bl_ui_units_x = 11

    def draw(self, context):
        _draw_display(self.layout, context)


class CLOTHNEXT_PT_pattern_arrange_popover(bpy.types.Panel):
    """Arrangement controls in the header of the workspace's 3D view."""
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"
    bl_label = "Arrange Panels"
    bl_ui_units_x = 11

    def draw(self, context):
        layout = self.layout
        layout.label(text="Selected panels")
        _draw_arrange(layout, context)
        settings = context.scene.cloth_next_pattern_settings
        layout.use_property_split = True
        layout.prop(settings, "staging_offset")
        layout.prop(settings, "show_seams", text="Show Seams", **_icon_kwargs("seams"))


class CLOTHNEXT_MT_pattern_arrange(bpy.types.Menu):
    """Arrangement of the selected panels around the avatar"""
    bl_label = "Arrange"
    bl_idname = "CLOTHNEXT_MT_pattern_arrange"

    def draw(self, context):
        layout = self.layout
        op = layout.operator("clothnext.pattern_place", text="Place in Front", icon="AXIS_FRONT")
        op.side = "FRONT"
        op = layout.operator("clothnext.pattern_place", text="Place Behind", icon="AXIS_FRONT")
        op.side = "BACK"
        layout.operator("clothnext.pattern_restage", text="Reset to Staging", icon="LOOP_BACK")
        layout.separator()
        layout.prop(context.scene.cloth_next_pattern_settings, "show_seams",
                    text="Show Seams on Panels")


def _header_3d(self, context):
    """An "Arrange" menu next to Object, in the workspace's 3D view only."""
    if not workspace.is_pattern_workspace(getattr(context, "workspace", None)):
        return
    if workspace.is_locked_view(context.area):
        return
    self.layout.menu("CLOTHNEXT_MT_pattern_arrange")


def _window_menu(self, context):
    self.layout.separator()
    self.layout.operator("clothnext.pattern_open_workspace", icon="WORKSPACE")


def _load_icons():
    try:
        import bpy.utils.previews as previews
    except ImportError:
        return
    from ..icon_registry import _load_pixels
    collection = previews.new()
    for name in _ICON_NAMES:
        path = _ICON_DIR / f"{name}_64.png"
        if path.is_file():
            try:
                _load_pixels(collection, name, path)
            except Exception:
                pass
    _previews["collection"] = collection


def register():
    _load_icons()
    bpy.types.TOPBAR_MT_window.append(_window_menu)
    menus = getattr(bpy.types, "VIEW3D_MT_editor_menus", None)
    if menus is not None:
        menus.append(_header_3d)


def unregister():
    menus = getattr(bpy.types, "VIEW3D_MT_editor_menus", None)
    if menus is not None:
        menus.remove(_header_3d)
    bpy.types.TOPBAR_MT_window.remove(_window_menu)
    collection = _previews["collection"]
    if collection is not None:
        import bpy.utils.previews as previews
        previews.remove(collection)
        _previews["collection"] = None


CLASSES = (CLOTHNEXT_PT_pattern_display_popover, CLOTHNEXT_PT_pattern_arrange_popover,
           CLOTHNEXT_MT_pattern_arrange) + dock.CLASSES
