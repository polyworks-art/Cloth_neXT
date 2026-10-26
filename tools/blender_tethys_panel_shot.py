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
ARGS.add_argument("--during-bake", action="store_true",
                  help="start Bake Wetness and capture the panel and Bake window while the engine runs")
ARGS.add_argument("--cancel", action="store_true", help="press the Bake window's Cancel after the capture")
ARGS.add_argument("--assume-gaia", action="store_true",
                  help="skip the add-on's GAIA selection check (test scenes; the engine still checks access)")
ARGS = ARGS.parse_args(sys.argv[sys.argv.index("--") + 1:])
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)

from cloth_next.blender import tethys_bake, tethys_ui, water_flow  # noqa: E402

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


def shot(name="tethys_panel.png"):
    window, area, region = properties()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=str(ARGS.output / name))


def desktop_shot(name):
    """The whole screen, so the separate Bake window is in the picture (Windows)."""
    import subprocess
    target = str(ARGS.output / name).replace("'", "''")
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                    "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
                    "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
                    "$g=[System.Drawing.Graphics]::FromImage($i);"
                    "$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size);"
                    f"$i.Save('{target}')"], check=False, timeout=30)


def start_bake():
    if ARGS.assume_gaia:
        from cloth_next.tethys import engine
        tethys_bake.availability = lambda refresh=False: engine.Availability(True)
    window, area, region = properties()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.clothnext.tethys_bake("INVOKE_DEFAULT")


def wait_for_engine():
    """Hold the step queue until the engine is part-way, then capture."""
    from cloth_next.bake.controller import shared_controller
    shown = tethys_bake.progress_display()
    if shown is None or not 0.3 < shown[0] < 0.9 or "left" not in shown[1]:
        STEPS.insert(0, wait_for_engine)
        return
    print("TETHYS progress:", shown, shared_controller.snapshot().to_dict())
    shot("tethys_panel_baking.png")
    desktop_shot("tethys_bake_window.png")
    if ARGS.cancel:
        from cloth_next.blender import solver_test
        solver_test.request_cancel()  # exactly what the Bake window's Cancel calls


def wait_for_finish():
    from cloth_next.bake.controller import shared_controller
    if tethys_bake.is_running() or shared_controller.snapshot().active:
        STEPS.insert(0, wait_for_finish)
        return
    print("TETHYS finished:", shared_controller.snapshot().state.value,
          bpy.data.objects[ARGS.object].cloth_next.tethys.status)


def redraw():
    # Custom preview icons load on first use and show from the next redraw.
    properties()[1].tag_redraw()


STEPS += [select, scroll, redraw, redraw, redraw, redraw, redraw]
if ARGS.during_bake:
    STEPS += [start_bake, wait_for_engine, wait_for_finish, redraw, redraw]
STEPS += [shot, lambda: bpy.ops.wm.quit_blender()]


def tick():
    if not STEPS:
        return None
    STEPS.pop(0)()
    return 0.25 if STEPS and STEPS[0] in (wait_for_engine, wait_for_finish) else 1.0


bpy.app.timers.register(tick, first_interval=2.0)
