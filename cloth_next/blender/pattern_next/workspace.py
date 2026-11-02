# SPDX-License-Identifier: GPL-3.0-or-later
"""The native "PATTERN NeXt" workspace tab.

Why a bundled template: Blender's Python API cannot create screen areas
directly, and ``workspace.duplicate``/``append_activate`` both activate the
new workspace (stealing focus) and copy whatever the user's current layout
is. ``resources/pattern_next/pattern_workspace.blend`` holds one curated
workspace (built by ``tools/build_pattern_workspace.py``); it is appended
with ``bpy.data.libraries.load`` which adds the tab without activating it.

Area identification: the 2D Pattern editor is a VIEW_3D area of a workspace
carrying ``WORKSPACE_MARKER`` whose RegionView3D has ``lock_rotation``
set. Blender's own rotation lock (the quad-view "Lock" flag, stored in the
.blend) makes orbit, roll, numpad axis views, perspective toggle and camera
view fail their poll, so the lock lives in Blender, not in a global event
filter. Ordinary 3D views never carry the flag, and other workspaces are
never touched. If a user removes the flag, the rightmost 3D view of the
PATTERN NeXt workspace is re-locked the next time the editor starts.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import bpy


_log = logging.getLogger(__name__)

WORKSPACE_NAME = "PATTERN NeXt"
WORKSPACE_MARKER = "cloth_next_pattern_workspace"
CONFIGURED_MARKER = "cloth_next_pattern_configured"
CONFIG_VERSION = 3  # 3: no sidebar; the inspector is docked in the 2D editor
TEMPLATE = (Path(__file__).resolve().parents[2] / "resources" / "pattern_next"
            / "pattern_workspace.blend")
# Front view: looking along +Y, canvas u -> world X, canvas v -> world Z.
FRONT = (math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0)

_refreshed = set()

_OBJECT_TYPES = ("mesh", "curve", "surface", "meta", "font", "pointcloud", "volume",
                 "grease_pencil", "armature", "lattice", "empty", "light", "light_probe",
                 "camera", "speaker", "curves")


def is_pattern_workspace(workspace):
    try:
        return bool(workspace is not None and workspace.get(WORKSPACE_MARKER))
    except ReferenceError:
        return False


def pattern_workspace():
    for workspace in bpy.data.workspaces:
        if is_pattern_workspace(workspace):
            return workspace
    return None


def is_locked_view(area):
    if area is None or area.type != "VIEW_3D":
        return False
    space = area.spaces.active
    rv3d = getattr(space, "region_3d", None)
    return bool(rv3d is not None and rv3d.lock_rotation and not space.region_quadviews)


def is_pattern_area(context, area=None):
    """True only for the 2D Pattern editor of the PATTERN NeXt workspace."""
    area = area if area is not None else getattr(context, "area", None)
    return is_pattern_workspace(getattr(context, "workspace", None)) and is_locked_view(area)


def window_pattern_areas(window, *, repair=False):
    """2D editor areas of ``window`` (empty unless it shows our workspace)."""
    try:
        if not is_pattern_workspace(window.workspace):
            return []
        screen = window.screen
    except ReferenceError:
        return []
    areas = [a for a in screen.areas if is_locked_view(a)]
    if not areas and repair:
        views = sorted((a for a in screen.areas if a.type == "VIEW_3D"), key=lambda a: a.x)
        if len(views) >= 2:
            configure_2d(views[-1].spaces.active, full=True)
            areas = [views[-1]]
    return areas


def window_region(area):
    for region in area.regions:
        if region.type == "WINDOW":
            return region
    return None


def is_front_ortho(rv3d):
    q = tuple(rv3d.view_rotation)
    return (rv3d.view_perspective == "ORTHO"
            and all(abs(a - b) < 1e-4 for a, b in zip(q, FRONT)))


def configure_2d(space, *, full=False):
    """Lock the view; on first use also apply the drafting display."""
    rv3d = space.region_3d
    if rv3d.view_perspective != "ORTHO":
        rv3d.view_perspective = "ORTHO"
    if not is_front_ortho(rv3d):
        rv3d.view_rotation = FRONT
    if not rv3d.lock_rotation:
        rv3d.lock_rotation = True
    if not full:
        return
    for name in _OBJECT_TYPES:
        attribute = f"show_object_viewport_{name}"
        if hasattr(space, attribute):
            setattr(space, attribute, False)
    overlay = space.overlay
    for name, value in (("show_floor", False), ("show_ortho_grid", False),
                        ("show_axis_x", False), ("show_axis_y", False),
                        ("show_axis_z", False), ("show_cursor", False),
                        ("show_object_origins", False), ("show_text", False),
                        ("show_stats", False), ("show_relationship_lines", False),
                        ("show_annotation", True)):
        if hasattr(overlay, name):
            setattr(overlay, name, value)
    for name, value in (("show_gizmo_navigate", False), ("show_region_toolbar", False),
                        ("show_region_tool_header", False), ("show_region_header", False),
                        ("show_region_ui", False)):
        if hasattr(space, name):
            setattr(space, name, value)
    space.shading.type = "SOLID"


def refresh_regions(area):
    """Apply hidden-region flags set while the screen was inactive.

    Flags written to a screen that is not shown in a window (the appended
    template) are stored but the regions keep their size until toggled.
    Each area is refreshed at most once: toggling again while Blender
    animates a region could leave it shown.
    """
    key = area.as_pointer()
    if key in _refreshed:
        return
    _refreshed.add(key)
    space = area.spaces.active
    sidebar = next((r for r in area.regions if r.type == "UI"), None)
    if sidebar is not None and hasattr(sidebar, "active_panel_category"):
        try:
            sidebar.active_panel_category = "PATTERN NeXt"
        except (TypeError, ValueError, AttributeError):
            pass
    for region_type, attribute in (("HEADER", "show_region_header"),
                                   ("TOOL_HEADER", "show_region_tool_header"),
                                   ("TOOLS", "show_region_toolbar")):
        region = next((r for r in area.regions if r.type == region_type), None)
        if region is not None and region.height > 1 and region.width > 1 \
                and not getattr(space, attribute, True):
            setattr(space, attribute, True)
            setattr(space, attribute, False)


def configure_screen(screen, *, force=False):
    if not force and screen.get(CONFIGURED_MARKER, 0) >= CONFIG_VERSION:
        return
    views = sorted((a for a in screen.areas if a.type == "VIEW_3D"), key=lambda a: a.x)
    locked = [a for a in views if is_locked_view(a)] or views[-1:]
    for area in locked:
        configure_2d(area.spaces.active, full=True)
        rv3d = area.spaces.active.region_3d
        rv3d.view_location = (0.0, 0.0, 0.6)
        rv3d.view_distance = 2.2
    screen[CONFIGURED_MARKER] = CONFIG_VERSION


LAYOUT_MARKER = "cloth_next_pattern_layout"
LAYOUT_VERSION = 1  # 1: only the 3D view and the 2D editor (no Outliner/Properties)


def close_side_areas(window):
    """Give the whole workspace to the 3D view and the 2D editor.

    Workspaces appended from an older template (or edited by the user before
    this layout) still carry the Outliner and Properties column; it is closed
    once per screen with Blender's own area operator. Areas the user adds
    later are left alone.
    """
    screen = window.screen
    if screen.get(LAYOUT_MARKER, 0) >= LAYOUT_VERSION:
        return
    for area in [a for a in screen.areas if a.type in {"OUTLINER", "PROPERTIES"}]:
        try:
            with bpy.context.temp_override(window=window, screen=screen, area=area):
                bpy.ops.screen.area_close()
        except RuntimeError:
            _log.warning("PATTERN NeXt: could not close the %s area", area.type)
    screen[LAYOUT_MARKER] = LAYOUT_VERSION


def _drop_template_library():
    for library in tuple(bpy.data.libraries):
        try:
            if Path(bpy.path.abspath(library.filepath)).resolve() == TEMPLATE.resolve():
                if not any(getattr(item, "library", None) == library
                           for item in getattr(library, "users_id", ())):
                    bpy.data.libraries.remove(library)
        except (OSError, ReferenceError, RuntimeError):
            continue


def ensure_workspace():
    """Append the workspace once per file; never activates it."""
    existing = pattern_workspace()
    if existing is not None:
        return existing
    if not TEMPLATE.is_file():
        _log.warning("PATTERN NeXt workspace template is missing: %s", TEMPLATE)
        return None
    with bpy.data.libraries.load(str(TEMPLATE), link=False) as (source, target):
        names = [name for name in source.workspaces if name == WORKSPACE_NAME]
        target.workspaces = names[:1]
    workspace = target.workspaces[0] if target.workspaces else None
    _drop_template_library()
    if workspace is None:
        return None
    workspace[WORKSPACE_MARKER] = 1
    for screen in getattr(workspace, "screens", ()):
        configure_screen(screen)
    return workspace


def _ensure_later():
    try:
        if not getattr(bpy.app, "background", False):
            ensure_workspace()
    except Exception:
        _log.exception("PATTERN NeXt workspace could not be prepared")
    return None


def on_load_post():
    """Called from the PATTERN NeXt load handler: add the tab to loaded files."""
    if not bpy.app.timers.is_registered(_ensure_later):
        bpy.app.timers.register(_ensure_later, first_interval=0.05)


class CLOTHNEXT_OT_pattern_open_workspace(bpy.types.Operator):
    """Open the PATTERN NeXt workspace (restores it if it was deleted)"""

    bl_idname = "clothnext.pattern_open_workspace"
    bl_label = "Open PATTERN NeXt"
    bl_options = {"REGISTER"}

    def execute(self, context):
        workspace = ensure_workspace()
        if workspace is None:
            self.report({"ERROR"}, "The PATTERN NeXt workspace template is missing")
            return {"CANCELLED"}
        context.window.workspace = workspace
        bpy.app.timers.register(_repair_active, first_interval=0.1)
        return {"FINISHED"}


def _repair_active():
    for window in bpy.context.window_manager.windows:
        if is_pattern_workspace(window.workspace):
            configure_screen(window.screen)
            for area in window_pattern_areas(window, repair=True):
                configure_2d(area.spaces.active)
    return None


def register():
    bpy.app.timers.register(_ensure_later, first_interval=0.1)


def unregister():
    if bpy.app.timers.is_registered(_ensure_later):
        bpy.app.timers.unregister(_ensure_later)
    if bpy.app.timers.is_registered(_repair_active):
        bpy.app.timers.unregister(_repair_active)


CLASSES = (CLOTHNEXT_OT_pattern_open_workspace,)
