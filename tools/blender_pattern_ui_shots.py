# SPDX-License-Identifier: GPL-3.0-or-later
"""Screenshot matrix of the PATTERN NeXt workspace in real Blender.

    blender --factory-startup --enable-event-simulate -p 0 0 1920 1080 \
        --python tools/blender_pattern_ui_shots.py -- <output-dir> [ui_scale] [prefix]

Builds a small garment (front, back, two sleeves, seams) through the real
operators, then captures: empty workspace, multiple panels, one selected
pattern, the Polygon tool mid-draft, the Sewing tool with a hovered edge,
visible triangulation, the inspector expanded and collapsed, a narrow 2D
editor and the light theme. Used for the UI redesign review.
"""
import json
import sys
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUTPUT = Path(argv[0]) if argv else ROOT / "docs/validation/pattern_next/ui"
SCALE = float(argv[1]) if len(argv) > 1 else 1.0
PREFIX = argv[2] if len(argv) > 2 else ""
OUTPUT.mkdir(parents=True, exist_ok=True)

import cloth_next  # noqa: E402
cloth_next.register()
from cloth_next.blender.pattern_next import draw, model, sync, workspace  # noqa: E402
from cloth_next.pattern_next.shapes import ContourPoint  # noqa: E402
from dataclasses import replace  # noqa: E402

shots = []


def window():
    return bpy.context.window_manager.windows[0]


def views():
    return sorted((a for a in window().screen.areas if a.type == "VIEW_3D"), key=lambda a: a.x)


def region_of(area):
    return next(r for r in area.regions if r.type == "WINDOW")


def shot(name):
    path = OUTPUT / f"{PREFIX}{name}.png"
    yield .5
    bpy.ops.screen.screenshot(filepath=str(path))
    shots.append(path.name)
    yield .3


def to_window(canvas):
    area = workspace.window_pattern_areas(window())[0]
    region = region_of(area)
    xy = draw.canvas_to_region(region, area.spaces.active.region_3d, canvas)
    return round(region.x + xy[0]), round(region.y + xy[1])


def move(x, y, steps=3):
    start = getattr(move, "last", (x, y))
    for i in range(1, steps + 1):
        window().event_simulate(type="MOUSEMOVE", value="NOTHING",
                                x=round(start[0] + (x - start[0]) * i / steps),
                                y=round(start[1] + (y - start[1]) * i / steps))
        yield .03
    move.last = (x, y)


def click(x, y):
    yield from move(x, y)
    window().event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    yield .03
    window().event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
    yield .08


def key(kind, **mods):
    x, y = getattr(move, "last", (10, 10))
    window().event_simulate(type=kind, value="PRESS", x=x, y=y, **mods)
    yield .03
    window().event_simulate(type=kind, value="RELEASE", x=x, y=y, **mods)
    yield .08


def curved(points, index, out, back):
    pts = list(points)
    pts[index] = replace(pts[index], curve_out=True, handle_out=out)
    nxt = (index + 1) % len(pts)
    pts[nxt] = replace(pts[nxt], handle_in=back)
    return tuple(pts)


def garment():
    scene = bpy.context.scene
    front = tuple(ContourPoint(0, p) for p in [(-0.25, 0.0), (0.25, 0.0), (0.27, 0.42), (0.09, 0.62),
                                               (0.0, 0.55), (-0.09, 0.62), (-0.27, 0.42)])
    front = curved(front, 3, (-0.03, -0.04), (0.03, 0.0))
    front = curved(front, 4, (-0.03, 0.0), (0.03, -0.04))
    back = tuple(replace(p, co=(p.co[0] + 0.7, p.co[1])) for p in front)
    sleeve = tuple(ContourPoint(0, p) for p in [(-0.2, 0.0), (0.2, 0.0), (0.17, 0.36), (-0.17, 0.36)])
    sleeve = curved(sleeve, 2, (-0.04, 0.12), (0.04, 0.12))
    made = []
    for name, points, offset in (("Front", front, (-0.35, 0.05)), ("Back", back, (-0.35, 0.05)),
                                 ("Sleeve L", sleeve, (-0.45, 0.85)), ("Sleeve R", sleeve, (0.45, 0.85))):
        pts = tuple(replace(p, co=(p.co[0] + offset[0], p.co[1] + offset[1])) for p in points)
        pattern = model.new_pattern(scene, name, pts)
        sync.rebuild(scene, pattern, force=True)
        made.append(pattern.identifier)
    f, b = model.find(scene, made[0]), model.find(scene, made[1])
    of, ob = model.outline(f), model.outline(b)
    # Front right side <-> back left side, front left side <-> back right side.
    for (a_start, a_end, b_start, b_end) in ((1, 2, 6, 0), (6, 0, 1, 2)):
        bpy.ops.clothnext.pattern_seam_add(
            a_pattern=f.identifier, a_start=of[a_start].uid, a_end=of[a_end].uid,
            b_pattern=b.identifier, b_start=ob[b_start].uid, b_end=ob[b_end].uid, flipped=True)
    return made


def frame_both():
    area = workspace.window_pattern_areas(window())[0]
    from cloth_next.blender.pattern_next import editor
    with bpy.context.temp_override(window=window(), area=area, region=region_of(area)):
        editor.frame(bpy.context, region_of(area), area.spaces.active.region_3d,
                     list(bpy.context.scene.cloth_next_patterns))
    left = views()[0].spaces.active.region_3d
    objects = [p.mesh_object for p in bpy.context.scene.cloth_next_patterns]
    left.view_location = sum((o.matrix_world.translation for o in objects),
                             objects[0].matrix_world.translation * 0) / len(objects)
    left.view_distance = 3.2


def sidebar(_area, show):
    """The PATTERN NeXt inspector column (not Blender's N sidebar)."""
    bpy.context.scene.cloth_next_pattern_settings.show_inspector = show


def run():
    bpy.context.preferences.view.ui_scale = SCALE
    for name in ("Cube",):
        if name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    yield 1.0
    window().workspace = workspace.pattern_workspace()
    yield 1.2
    yield from shot("01_empty")
    ids = garment()
    frame_both()
    yield .6
    yield from shot("02_garment_unselected")
    from cloth_next.blender.pattern_next.editor import select_pattern
    with bpy.context.temp_override(window=window()):
        select_pattern(bpy.context, model.find(bpy.context.scene, ids[0]))
    right = workspace.window_pattern_areas(window())[0]
    sidebar(right, True)
    yield .5
    yield from move(*to_window((0.0, 0.3)))
    yield from key("Z")
    yield .3
    yield from move(*to_window((-0.6, 0.25)))
    yield .3
    yield from shot("03_selected_edit_inspector")
    yield from key("H")
    yield .3
    for p in [(0.15, 1.0), (0.4, 0.98), (0.45, 1.22)]:
        yield from click(*to_window(p))
    yield from move(*to_window((0.2, 1.27)))
    yield .3
    yield from shot("04_polygon_tool")
    yield from key("ESC")
    yield .3
    yield from key("M")
    yield .3
    yield from move(*to_window((-0.1, 0.05)))
    yield .3
    yield from shot("05_sewing_tool")
    bpy.context.scene.cloth_next_pattern_settings.show_mesh = True
    yield .3
    yield from shot("06_triangulation")
    bpy.context.scene.cloth_next_pattern_settings.show_mesh = False
    sidebar(right, False)
    yield .3
    yield from shot("07_inspector_collapsed")
    # Light theme (Blender's bundled "Blender Light" preset).
    try:
        preset = Path(bpy.utils.resource_path("LOCAL")) / "scripts" / "presets" / \
            "interface_theme" / "Blender_Light.xml"
        bpy.ops.script.execute_preset(filepath=str(preset), menu_idname="USERPREF_MT_interface_theme_presets")
        yield .6
        yield from shot("09_light_theme")
    except Exception:
        traceback.print_exc()


def runner():
    try:
        yield from run()
    except Exception:
        traceback.print_exc()
    finally:
        (OUTPUT / f"{PREFIX}shots.json").write_text(json.dumps(shots, indent=1))
        print("SHOTS", shots, flush=True)
        bpy.ops.wm.quit_blender()


_steps = runner()


def _tick():
    try:
        return next(_steps)
    except StopIteration:
        return None


bpy.app.timers.register(_tick, first_interval=1.5)
