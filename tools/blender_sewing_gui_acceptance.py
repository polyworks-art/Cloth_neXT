# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-GUI Sewing acceptance run driven by simulated mouse and keyboard input.

Run with a visible Blender window (not --background):

    blender --factory-startup --enable-event-simulate \
        --python tools/blender_sewing_gui_acceptance.py -- <output-dir> [dense-cuts]

Every interaction goes through Blender's real event queue, modal handler,
toolbar hit testing and draw callbacks. Screenshots are written to the
output directory together with ``acceptance.json``.
"""
import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy
from mathutils import Euler, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUTPUT = Path(argv[0]) if argv else ROOT / "docs/validation/sewing/gui-acceptance"
DENSE_CUTS = int(argv[1]) if len(argv) > 1 else 300
OUTPUT.mkdir(parents=True, exist_ok=True)

import cloth_next  # noqa: E402
cloth_next.register()
from cloth_next.blender import sewing  # noqa: E402
from cloth_next.blender import object_attachments  # noqa: E402

results = {"checks": [], "timings": {}, "screenshots": []}


def check(name, condition, detail=""):
    results["checks"].append({"name": name, "ok": bool(condition), "detail": str(detail)})
    print(("PASS " if condition else "FAIL ") + name + (f" ({detail})" if detail else ""),
          flush=True)


def make_grid(name, cuts, size, location, rotation=(0, 0, 0), modifiers=()):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cuts, y_subdivisions=cuts, size=size,
                                    location=location, rotation=rotation)
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.clothnext.add_physics()
    for kind in modifiers:
        modifier = obj.modifiers.new(kind, kind)
        if kind == "SOLIDIFY":
            modifier.thickness = 0.03
    return obj


def scene_setup():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bodice = make_grid("Bodice", 24, 2.0, (0, 0, 0), modifiers=("SOLIDIFY",))
    sleeve = make_grid("Sleeve", 9, 1.0, (2.2, 0, 0))
    bpy.ops.mesh.primitive_uv_sphere_add(radius=.35, location=(-.2, -1.6, .6))
    body = bpy.context.active_object
    body.name = "Body"
    dense = make_grid("Dense", DENSE_CUTS, 2.0, (0, 0, -6))
    for obj in (bodice, sleeve, body, dense):
        obj.select_set(False)
    bpy.context.view_layer.objects.active = bodice
    return bodice, sleeve, body, dense


def view3d():
    window = bpy.context.window_manager.windows[0]
    for area in window.screen.areas:
        if area.type == "VIEW_3D":
            region = next(r for r in area.regions if r.type == "WINDOW")
            return window, area, region
    raise RuntimeError("No 3D View")


def set_view(rotation, location=(0, 0, 0), distance=6.0):
    _window, area, _region = view3d()
    space = area.spaces.active
    space.region_3d.view_rotation = Euler(rotation).to_quaternion()
    space.region_3d.view_location = location
    space.region_3d.view_distance = distance
    space.region_3d.view_perspective = "PERSP"
    space.overlay.show_floor = False
    space.show_region_toolbar = False
    space.show_region_ui = False
    area.tag_redraw()


def window_point(world):
    from bpy_extras import view3d_utils
    _window, area, region = view3d()
    point = view3d_utils.location_3d_to_region_2d(region, area.spaces.active.region_3d,
                                                  Vector(world))
    if point is None:
        raise RuntimeError(f"{world} is not on screen")
    return region.x + int(round(point.x)), region.y + int(round(point.y))


def world(obj, local):
    return tuple(obj.matrix_world @ Vector(local))


def editor():
    return next(iter(sewing._editor_sessions.values()), None)


def definitions():
    return bpy.context.scene.cloth_next_sewing_definitions


# --- generator steps: yield a delay in seconds -----------------------------

def move(x, y, steps=4):
    window = bpy.context.window_manager.windows[0]
    start = getattr(move, "last", (x, y))
    for i in range(1, steps + 1):
        px = round(start[0] + (x - start[0]) * i / steps)
        py = round(start[1] + (y - start[1]) * i / steps)
        window.event_simulate(type="MOUSEMOVE", value="NOTHING", x=px, y=py)
        yield .02
    move.last = (x, y)


def click(x, y):
    yield from move(x, y)
    window = bpy.context.window_manager.windows[0]
    window.event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    yield .03
    window.event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
    yield .05


def key(kind, **modifiers):
    window = bpy.context.window_manager.windows[0]
    x, y = getattr(move, "last", (10, 10))
    window.event_simulate(type=kind, value="PRESS", x=x, y=y, **modifiers)
    yield .03
    window.event_simulate(type=kind, value="RELEASE", x=x, y=y, **modifiers)
    yield .06


def toolbar_click(identifier):
    op = editor()
    _window, _area, region = view3d()
    layout = op.layouts.get(region.as_pointer()) if op else None
    if layout is None:
        raise RuntimeError("Toolbar has not been drawn")
    placed = layout.placed(identifier)
    if placed is None:
        raise RuntimeError(f"Toolbar has no {identifier} button")
    x = region.x + int(placed.x + placed.width / 2)
    y = region.y + int(placed.y + placed.height / 2)
    yield from click(x, y)


def screenshot(name):
    path = OUTPUT / f"{name}.png"
    bpy.ops.screen.screenshot(filepath=str(path))
    results["screenshots"].append(path.name)
    yield .2


def side_point(obj, side, fraction=.37, half=1.0):
    """World point on a grid boundary side of a size-2 (half=1) grid."""
    coordinate = -half + 2 * half * fraction
    local = {"y_min": (coordinate, -half, 0), "y_max": (coordinate, half, 0),
             "x_min": (-half, coordinate, 0), "x_max": (half, coordinate, 0)}[side]
    return window_point(world(obj, local))


def run():
    bodice, sleeve, body, dense = scene_setup()
    # Oblique view from below/behind: the reported "only one side" failure.
    set_view((math.radians(118), 0, math.radians(25)), (1.0, 0, 0), 7.5)
    yield 1.0
    window, area, region = view3d()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.clothnext.edit_sewing("INVOKE_DEFAULT", mode="SEGMENT")
    yield .5
    check("editor starts", editor() is not None)
    yield from screenshot("01-idle")

    # A/B: two intra-object seams on a Solidify garment seen from behind.
    yield from move(*side_point(bodice, "y_min"))
    yield .2
    check("hover from back side", editor().hover is not None and editor().hover.obj == bodice)
    yield from screenshot("02-hover-side-a")
    yield from click(*side_point(bodice, "y_min"))
    check("side A selected", editor().stage == "SELECT_SIDE_B", editor().stage)
    yield from move(*side_point(bodice, "y_max"))
    yield .2
    yield from screenshot("03-live-preview-same-object")
    yield from click(*side_point(bodice, "y_max"))
    check("preview after side B", editor().stage == "PREVIEW", editor().message)
    yield from key("RET")
    check("A intra seam committed and editor stays", len(definitions()) == 1 and editor() is not None
          and editor().stage == "IDLE", len(definitions()))
    yield from click(*side_point(bodice, "x_min"))
    yield from click(*side_point(bodice, "x_max"))
    yield from key("F")
    yield from key("RET")
    check("B second intra seam without restart", len(definitions()) == 2
          and definitions()[1].flipped, len(definitions()))

    # C: two consecutive cross-object seams.
    for side in ("y_min", "y_max"):
        yield from click(*side_point(sleeve, side, .5, .5))
        yield from click(*side_point(bodice, side, .8))
        yield from key("RET")
    check("C two cross-object seams", len(definitions()) == 4
          and definitions()[2].source_persistent_id != definitions()[2].target_persistent_id,
          len(definitions()))
    yield from screenshot("04-four-seams")

    # H: cancel at each stage leaves no definition behind.
    count = len(definitions())
    yield from click(*side_point(sleeve, "x_min", .5, .5))
    yield from key("ESC")
    check("H cancel after side A", len(definitions()) == count and editor().stage == "IDLE")
    yield from click(*side_point(sleeve, "x_min", .5, .5))
    yield from click(*side_point(sleeve, "x_max", .5, .5))
    yield from key("ESC")
    check("H cancel in preview", len(definitions()) == count and editor() is not None)

    # Invalid: Side B identical to Side A is explained in red and not saved.
    yield from click(*side_point(sleeve, "x_min", .5, .5))
    yield from click(*side_point(sleeve, "x_min", .5, .5))
    yield .2
    check("invalid same path is flagged", editor().invalid and editor().message, editor().message)
    yield from screenshot("05-invalid")
    yield from key("RET")
    check("invalid seam is not committed", len(definitions()) == count)
    yield from key("ESC")

    # D: Free Sewing through an explicit interior point, via toolbar buttons.
    yield from toolbar_click("mode:FREE")
    check("toolbar switches to Free", editor().mode == "FREE", editor().mode)
    start, bend, end = (-1, -1, 0), (0.0, -0.25, 0), (1, -1, 0)
    for local in (start, bend, end):
        yield from click(*window_point(world(bodice, local)))
    path = list(editor().a)
    rest = [tuple(round(c, 4) for c in bodice.data.vertices[i].co) for i in path]
    check("D Free path keeps the explicit interior point",
          (0.0, -0.25, 0.0) in [tuple(round(v, 4) for v in p) for p in rest], len(path))
    yield from screenshot("06-free-side-a")
    yield from toolbar_click("confirm")
    yield from click(*window_point(world(sleeve, (-.5, .5, 0))))
    yield from click(*window_point(world(sleeve, (.5, .5, 0))))
    yield from toolbar_click("confirm")
    check("Free preview", editor().stage == "PREVIEW", editor().stage)
    yield from toolbar_click("confirm")
    check("D Free seam committed", len(definitions()) == count + 1, len(definitions()))

    # G: Multi 1:N.
    yield from toolbar_click("mode:MULTI")
    yield from click(*side_point(sleeve, "x_min", .5, .5))
    yield from key("RET")
    yield from click(*side_point(sleeve, "x_max", .5, .5))
    yield from click(*side_point(bodice, "x_min", .5))
    yield from key("RET")
    yield from screenshot("07-multi-preview")
    before = len(definitions())
    yield from key("RET")
    check("G Multi 1:N created two seams", len(definitions()) == before + 2,
          len(definitions()) - before)

    # E: Edit mode selection and reversal through the toolbar.
    yield from toolbar_click("mode:EDIT")
    target_index = 0
    item = definitions()[target_index]
    flipped, mapping = item.flipped, [(r.source_index, r.target_index) for r in item.mapping]
    yield from click(*side_point(bodice, "y_min", .55))
    check("Edit selects seam in viewport", bpy.context.scene.cloth_next_sewing_index == target_index,
          bpy.context.scene.cloth_next_sewing_index)
    yield from toolbar_click("reverse")
    item = definitions()[target_index]
    new_mapping = [(r.source_index, r.target_index) for r in item.mapping]
    check("E reverse changes direction and mapping", item.flipped != flipped and new_mapping != mapping)
    strength = item.strength
    yield from toolbar_click("strength_down")
    check("Edit strength button", abs(definitions()[target_index].strength - (strength - 10)) < 1e-6)
    yield from screenshot("08-edit")

    # Navigation passes through, editor survives.
    yield from key("WHEELUPMOUSE")
    check("navigation keeps editor", editor() is not None)

    # Performance on a dense garment.
    set_view((math.radians(60), 0, math.radians(20)), (0, 0, -6), 5.5)
    yield .8
    yield from toolbar_click("mode:SEGMENT")
    timings = []
    original = sewing.CLOTHNEXT_OT_edit_sewing._pick

    def timed(self, context, event):
        begin = time.perf_counter()
        try:
            return original(self, context, event)
        finally:
            timings.append(time.perf_counter() - begin)
    sewing.CLOTHNEXT_OT_edit_sewing._pick = timed

    for i in range(60):
        fraction = .1 + .8 * i / 59
        yield from move(*side_point(dense, "y_min", fraction), steps=1)
    yield from click(*side_point(dense, "y_min", .3))
    for i in range(40):
        yield from move(*side_point(dense, "y_max", .1 + .8 * i / 39), steps=1)
    yield from click(*side_point(dense, "y_max", .6))
    yield from key("RET")
    sewing.CLOTHNEXT_OT_edit_sewing._pick = original
    steady = sorted(timings[3:]) or [0]
    results["timings"]["dense_vertices"] = len(dense.data.vertices)
    results["timings"]["pick_first_ms"] = round(timings[0] * 1000, 2) if timings else None
    results["timings"]["pick_median_ms"] = round(steady[len(steady) // 2] * 1000, 2)
    results["timings"]["pick_p95_ms"] = round(steady[int(len(steady) * .95)] * 1000, 2)
    check("dense hover stays responsive", results["timings"]["pick_p95_ms"] < 16,
          results["timings"])
    # Overlay draw cost with all seams visible, against the same view without it.
    def redraw_ms(count=30):
        values = []
        for _ in range(count):
            begin = time.perf_counter()
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.wm.redraw_timer(type="DRAW", iterations=1)
            values.append(time.perf_counter() - begin)
        values.sort()
        return round(values[len(values) // 2] * 1000, 2)
    results["timings"]["redraw_with_sewing_ms"] = redraw_ms()
    import cProfile, io, pstats
    profiler = cProfile.Profile()
    profiler.enable()
    redraw_ms(10)
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(18)
    (OUTPUT / "draw-profile.txt").write_text(stream.getvalue(), encoding="utf-8")
    bpy.context.scene.cloth_next_show_sewing = False
    results["timings"]["redraw_without_sewing_ms"] = redraw_ms()
    bpy.context.scene.cloth_next_show_sewing = True
    overhead = (results["timings"]["redraw_with_sewing_ms"]
                - results["timings"]["redraw_without_sewing_ms"])
    check("overlay redraw overhead is small", overhead < 8, f"{overhead:.2f} ms")
    # The toolbar follows the sidebar (N panel).
    layout = editor().layouts.get(region.as_pointer())
    right_closed = layout.x + layout.width
    area.spaces.active.show_region_ui = True
    area.tag_redraw()
    yield .6
    layout = editor().layouts.get(region.as_pointer())
    right_open = layout.x + layout.width
    yield from screenshot("09b-sidebar-open")
    check("toolbar moves with the N panel", right_open < right_closed - 100,
          f"{right_closed:.0f} -> {right_open:.0f}")
    area.spaces.active.show_region_ui = False
    area.tag_redraw()
    yield .4
    yield from screenshot("09-dense")

    # Undo/redo of a committed seam: Ctrl+Z while idle leaves the editor cleanly.
    set_view((math.radians(118), 0, math.radians(25)), (1.0, 0, 0), 7.5)
    yield .6
    count = len(definitions())
    yield from click(*side_point(sleeve, "x_min", .5, .5))
    yield from click(*side_point(bodice, "x_max", .3))
    yield from key("RET")
    check("seam before undo", len(definitions()) == count + 1)
    yield from key("Z", ctrl=True)
    yield .5
    check("Ctrl+Z undoes the last seam and exits cleanly",
          len(bpy.context.scene.cloth_next_sewing_definitions) == count and editor() is None,
          len(bpy.context.scene.cloth_next_sewing_definitions))
    yield from key("Z", ctrl=True, shift=True)
    yield .5
    check("Ctrl+Shift+Z redoes it", len(bpy.context.scene.cloth_next_sewing_definitions) == count + 1)
    bodice, sleeve, dense = (bpy.data.objects[name] for name in ("Bodice", "Sleeve", "Dense"))
    window, area, region = view3d()
    with bpy.context.temp_override(window=window, area=area, region=region):
        bpy.ops.clothnext.edit_sewing("INVOKE_DEFAULT", mode="SEGMENT")
    yield .5

    # Done button exits and removes editor handlers.
    yield from toolbar_click("done")
    check("Done exits editor", editor() is None and sewing._editor_view_handle is None)

    # O: export receives every definition, including several on one object.
    from types import SimpleNamespace
    from cloth_next.attachments import topology_fingerprint, wire_entry
    entries = []
    for obj in (bodice, sleeve, dense):
        vertices, _edges, triangles = sewing._mesh(obj)
        entries.append(SimpleNamespace(obj=obj, role="CLOTH", boundary_vertices=vertices,
                                       boundary_triangles=triangles))
    _pairs, stitches = sewing.snapshot_enabled(bpy.context.scene, entries)
    identifiers = {attachment.identifier.removesuffix("_reverse") for attachment, *_ in stitches}
    check("O export contains every enabled seam",
          identifiers == {item.identifier for item in definitions() if item.enabled and item.strength > 0},
          f"{len(identifiers)} of {len(definitions())}")
    # K: exported strength matches UI strength.
    by_id = {item.identifier: item for item in definitions()}
    check("K exported strength equals UI strength", all(
        abs(wire_entry(a, source_vertex_count=s, target_vertex_count=t)["stitch_stiffness"]
            - by_id[a.identifier.removesuffix("_reverse")].strength * 500.0) < 1e-6
        for a, s, t in stitches))

    # I: save and reload.
    path = OUTPUT / "sewing-acceptance.blend"
    snapshot = [(d.identifier, [r.index for r in d.side_a], [r.index for r in d.side_b], d.flipped,
                 d.strength) for d in definitions()]
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    yield 1.0
    reloaded = [(d.identifier, [r.index for r in d.side_a], [r.index for r in d.side_b],
                 d.flipped, d.strength) for d in bpy.context.scene.cloth_next_sewing_definitions]
    check("I save/reload preserves every seam", reloaded == snapshot, len(reloaded))

    # Object Attachment toolbar smoke.
    bodice = bpy.data.objects["Bodice"]
    window, area, region = view3d()
    bpy.context.view_layer.objects.active = bodice
    with bpy.context.temp_override(window=window, area=area, region=region,
                                   active_object=bodice, object=bodice):
        bpy.ops.clothnext.edit_attachment("INVOKE_DEFAULT")
    yield .6
    attachment_editor = next(iter(object_attachments._editor_sessions.values()), None)
    check("attachment editor shows toolbar", attachment_editor is not None
          and bool(getattr(attachment_editor, "layouts", {})))
    yield from screenshot("10-attachment-toolbar")
    if attachment_editor is not None:
        yield from key("ESC")
        yield from key("ESC")

    # N: unregister/register lifecycle with no leftovers.
    cloth_next.unregister()
    check("N unregister removes sewing handlers", sewing._draw_handle is None
          and not sewing._editor_sessions)
    cloth_next.register()
    check("N register again", hasattr(bpy.types.Scene, "cloth_next_sewing_definitions"))


def driver():
    generator = run()

    def tick():
        try:
            return next(generator)
        except StopIteration:
            finish()
        except Exception:
            results["error"] = traceback.format_exc()
            print(results["error"], flush=True)
            finish()
        return None
    bpy.app.timers.register(tick, first_interval=1.5, persistent=True)


def finish():
    results["passed"] = all(row["ok"] for row in results["checks"]) and "error" not in results
    (OUTPUT / "acceptance.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    print("SEWING_GUI_ACCEPTANCE", "PASSED" if results["passed"] else "FAILED", flush=True)
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=.5)


driver()
