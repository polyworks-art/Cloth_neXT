# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshot the TETHYS Wetness panel of a scene's WetSheet (UI review).

    blender <scene.blend> --python tools/blender_tethys_panel_shot.py -- --repo <repo> --output <dir> [--open Detail Advanced]

Opens GAIA Water Flow and TETHYS Wetness (plus the named sub-panels),
shows the Physics tab (--full maximises the editor), scrolls the panel into
view and writes tethys_panel.png. Needs a window (no -b).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import addon_utils
import bpy

ARGS = argparse.ArgumentParser()
ARGS.add_argument("--repo", type=Path, required=True)
ARGS.add_argument("--output", type=Path, required=True)
ARGS.add_argument("--object", default="WetSheet")
ARGS.add_argument("--open", nargs="*", default=[])
ARGS.add_argument("--full", action="store_true", help="maximise the Properties editor")
ARGS = ARGS.parse_args(sys.argv[sys.argv.index("--") + 1:])
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)

from cloth_next.blender import tethys_ui, water_flow  # noqa: E402

OPEN = {"CLOTHNEXT_PT_water_flow", "CLOTHNEXT_PT_tethys"} | {name if name.startswith("CLOTHNEXT_PT_") else f"CLOTHNEXT_PT_tethys_{name.lower()}" for name in ARGS.open}
for cls in [*water_flow.CLASSES, *tethys_ui.CLASSES]:
    if not issubclass(cls, bpy.types.Panel):
        continue
    if getattr(cls, "bl_idname", "") in OPEN:
        bpy.utils.unregister_class(cls)
        cls.bl_options = set(getattr(cls, "bl_options", set())) - {"DEFAULT_CLOSED"}
        bpy.utils.register_class(cls)
# Re-register children after their parents so the hierarchy is intact.
for cls in [*water_flow.CLASSES, *tethys_ui.CLASSES]:
    if (issubclass(cls, bpy.types.Panel) and cls.bl_idname not in OPEN
            and getattr(cls, "bl_parent_id", "")):
        bpy.utils.unregister_class(cls)
        bpy.utils.register_class(cls)
# TETHYS last, as in the add-on's own registration order.
panels = [cls for cls in tethys_ui.CLASSES if issubclass(cls, bpy.types.Panel)]
for cls in reversed(panels):
    bpy.utils.unregister_class(cls)
for cls in panels:
    bpy.utils.register_class(cls)

STEPS = []


def properties():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "PROPERTIES")
    return window, area, next(r for r in area.regions if r.type == "WINDOW")


def select():
    obj = bpy.data.objects[ARGS.object]
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    window, area, region = properties()
    area.spaces.active.context = "PHYSICS"
    if ARGS.full:
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.screen.screen_full_area()


def scroll():
    window, area, region = properties()
    with bpy.context.temp_override(window=window, area=area, region=region):
        for _ in range(200):
            bpy.ops.view2d.scroll_down(deltay=40)


def shot():
    window, area, region = properties()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=str(ARGS.output / "tethys_panel.png"))


def redraw():
    # Custom preview icons load on first use and show from the next redraw.
    properties()[1].tag_redraw()


STEPS += [select, scroll, redraw, redraw, redraw, redraw, redraw, shot, lambda: bpy.ops.wm.quit_blender()]


def tick():
    if not STEPS:
        return None
    STEPS.pop(0)()
    return 1.0


bpy.app.timers.register(tick, first_interval=2.0)
