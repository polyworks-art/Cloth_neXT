# SPDX-License-Identifier: GPL-3.0-or-later
"""Build cloth_next/resources/pattern_next/pattern_workspace.blend.

Run in a *windowed* Blender (screen operators need a window):

    blender --factory-startup --python tools/build_pattern_workspace.py

The script duplicates the factory "Layout" workspace, removes the timeline,
splits the 3D View into a 3D Garment view (left) and the 2D Pattern editor
(right, front orthographic with Blender's rotation lock), writes only that
workspace (and its screen) to the template file and quits. The add-on
applies the remaining drafting display settings on first use
(``workspace.configure_screen``), so the template stays minimal.
"""

import math
import os
import sys
import traceback

import bpy

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "cloth_next", "resources", "pattern_next", "pattern_workspace.blend")
NAME = "PATTERN NeXt"
MARKER = "cloth_next_pattern_workspace"
FRONT = (math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0)
_state = {"code": 1}


def _window():
    return bpy.context.window_manager.windows[0]


def _quit():
    bpy.ops.wm.quit_blender()


def step_duplicate():
    try:
        window = _window()
        # Factory startup opens on Layout. Switching workspaces right before
        # duplicating crashes 5.2.3 (the switch is applied lazily).
        assert window.workspace.name == "Layout", window.workspace.name
        with bpy.context.temp_override(window=window, screen=window.screen):
            bpy.ops.workspace.duplicate()
        bpy.app.timers.register(step_layout, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        _quit()


def step_layout():
    try:
        window = _window()
        workspace, screen = window.workspace, window.screen
        assert workspace.name != "Layout", "duplicate did not activate"
        # Renaming the screen here crashes Blender 5.2.3 (layout lookup by
        # screen name); the workspace name is what the tab bar shows.
        workspace.name = NAME
        for area in [a for a in screen.areas if a.type in {"DOPESHEET_EDITOR", "TIMELINE"}]:
            with bpy.context.temp_override(window=window, screen=screen, area=area):
                bpy.ops.screen.area_close()
        view = max((a for a in screen.areas if a.type == "VIEW_3D"),
                   key=lambda a: a.width * a.height)
        region = next(r for r in view.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=window, screen=screen, area=view, region=region):
            bpy.ops.screen.area_split(direction="VERTICAL", factor=0.5)
        bpy.app.timers.register(step_write, first_interval=0.5)
    except Exception:
        traceback.print_exc()
        _quit()


def step_write():
    try:
        window = _window()
        screen = window.screen
        views = sorted((a for a in screen.areas if a.type == "VIEW_3D"), key=lambda a: a.x)
        assert len(views) == 2, views
        rv3d = views[1].spaces.active.region_3d
        rv3d.view_perspective = "ORTHO"
        rv3d.view_rotation = FRONT
        rv3d.lock_rotation = True
        rv3d.view_location = (0.0, 0.0, 0.6)
        rv3d.view_distance = 2.2
        workspace = window.workspace
        workspace[MARKER] = 1
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        bpy.data.libraries.write(OUT, {workspace}, fake_user=False, compress=True)
        with bpy.data.libraries.load(OUT) as (source, _target):
            assert source.workspaces == [NAME], source.workspaces
        print(f"PATTERN NeXt workspace template written: {OUT} ({os.path.getsize(OUT)} bytes)")
        _state["code"] = 0
    except Exception:
        traceback.print_exc()
    _quit()


bpy.app.timers.register(step_duplicate, first_interval=1.0)
