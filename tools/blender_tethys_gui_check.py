# SPDX-License-Identifier: GPL-3.0-or-later
"""Visible Blender check of baked TETHYS wetness: scrubbing, EEVEE Material Preview, debug overlay, panel.

    blender <baked scene.blend> --python tools/blender_tethys_gui_check.py -- --repo <repo> --output <dir>

Steps the timeline like an artist scrubbing, screenshots the 3D Viewport in
Material Preview (EEVEE) and with the Saturation overlay, screenshots the
TETHYS panel, writes report.json and quits. Needs a window (no -b).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import addon_utils
import bpy

ARGS = argparse.ArgumentParser()
ARGS.add_argument("--repo", type=Path, required=True)
ARGS.add_argument("--output", type=Path, required=True)
ARGS = ARGS.parse_args(sys.argv[sys.argv.index("--") + 1:])
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)

REPORT = {"shots": []}
FRAMES = [1, 20, 30, 40, 51, 60, 72]
STEPS = []


def areas(kind):
    window = bpy.context.window_manager.windows[0]
    return window, [a for a in window.screen.areas if a.type == kind]


def setup():
    window, views = areas("VIEW_3D")
    view = max(views, key=lambda a: a.width * a.height)
    space = view.spaces.active
    space.shading.type = "MATERIAL"
    space.overlay.show_overlays = True
    space.region_3d.view_perspective = "CAMERA"
    bpy.context.scene.render.engine = "BLENDER_EEVEE_NEXT" if hasattr(bpy.types, "RENDER_PT_eevee_next_sampling") \
        else bpy.context.scene.render.engine
    REPORT["view_size"] = [view.width, view.height]
    return window, view


def shot(name):
    window, view = setup()
    path = ARGS.output / f"{name}.png"
    with bpy.context.temp_override(window=window, area=view, region=next(r for r in view.regions if r.type == "WINDOW")):
        bpy.ops.screen.screenshot_area(filepath=str(path))
    image = bpy.data.images.load(str(path), check_existing=False)
    pixels = list(image.pixels)
    mean = sum(pixels[i] + pixels[i + 1] + pixels[i + 2] for i in range(0, len(pixels), 4)) / (len(pixels) / 4 * 3)
    bpy.data.images.remove(image)
    REPORT["shots"].append({"name": name, "frame": bpy.context.scene.frame_current, "mean": mean})


def panel_shot():
    window, views = areas("VIEW_3D")
    _w, props = areas("PROPERTIES")
    sheet = bpy.data.objects["WetSheet"]
    bpy.context.view_layer.objects.active = sheet
    sheet.select_set(True)
    if props:
        area = props[0]
        area.spaces.active.context = "PHYSICS"
    with bpy.context.temp_override(window=window):
        bpy.ops.screen.screenshot(filepath=str(ARGS.output / "window_with_panel.png"))


def plan():
    sheet = bpy.data.objects["WetSheet"]
    STEPS.append(setup)          # shading and camera first; the next redraw shows them
    for frame in FRAMES:
        STEPS.append(lambda f=frame: bpy.context.scene.frame_set(f))
        STEPS.append(lambda f=frame: shot(f"material_preview_f{f:03d}"))

    def overlay(mode):
        sheet.cloth_next.tethys.preview_mode = mode
        sheet.cloth_next.tethys.preview_scale = 1.0
    for mode in ("SATURATION", "ZONES", "SURFACE"):
        STEPS.append(lambda m=mode: (overlay(m), bpy.context.scene.frame_set(51)))
        STEPS.append(lambda m=mode: shot(f"overlay_{m.lower()}_f051"))
    STEPS.append(lambda: overlay("NONE"))
    STEPS.append(panel_shot)


def tick():
    if not STEPS:
        means = {s["name"]: s["mean"] for s in REPORT["shots"]}
        dry, wet = means.get("material_preview_f001"), means.get("material_preview_f051")
        REPORT["scrub_changes_image"] = dry is not None and wet is not None and abs(dry - wet) > 0.002
        (ARGS.output / "report.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
        bpy.ops.wm.quit_blender()
        return None
    step = STEPS.pop(0)
    try:
        step()
    except Exception as exc:  # noqa: BLE001 - recorded, the check continues
        REPORT.setdefault("errors", []).append(repr(exc))
    return 1.5   # give EEVEE time to redraw the new frame


plan()
bpy.app.timers.register(tick, first_interval=4.0)
