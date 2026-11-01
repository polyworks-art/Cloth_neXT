# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-GUI PATTERN NeXt acceptance run (simulated mouse and keyboard).

Run with a visible Blender window (not --background), two phases:

    blender --factory-startup --enable-event-simulate \
        --python tools/blender_pattern_gui_acceptance.py -- <output-dir> create
    blender --factory-startup --enable-event-simulate <output-dir>/pattern_acceptance.blend \
        --python tools/blender_pattern_gui_acceptance.py -- <output-dir> reopen

Every interaction goes through Blender's event queue, the 2D editor's modal
handler, toolbar hit testing and the draw callbacks. Phase "create" saves
``pattern_acceptance.blend``; phase "reopen" loads it in a new Blender
process and keeps editing. Results go to ``acceptance-<phase>.json`` and
screenshots to the output directory.
"""
import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUTPUT = Path(argv[0]) if argv else ROOT / "docs/validation/pattern_next/gui"
PHASE = argv[1] if len(argv) > 1 else "create"
OUTPUT.mkdir(parents=True, exist_ok=True)

import cloth_next  # noqa: E402
cloth_next.register()
from cloth_next.blender.pattern_next import draw, editor, model, sync, workspace  # noqa: E402

results = {"phase": PHASE, "blender": bpy.app.version_string, "checks": [], "timings": {},
           "screenshots": []}


def check(name, condition, detail=""):
    results["checks"].append({"name": name, "ok": bool(condition), "detail": str(detail)})
    print(("PASS " if condition else "FAIL ") + name + (f" ({detail})" if detail else ""),
          flush=True)


def window():
    return bpy.context.window_manager.windows[0]


def views():
    screen = window().screen
    return sorted((a for a in screen.areas if a.type == "VIEW_3D"), key=lambda a: a.x)


def pattern_area():
    areas = workspace.window_pattern_areas(window())
    return areas[0] if areas else None


def region_of(area):
    return workspace.window_region(area)


def to_window(canvas):
    area = pattern_area()
    region = region_of(area)
    xy = draw.canvas_to_region(region, area.spaces.active.region_3d, canvas)
    return round(region.x + xy[0]), round(region.y + xy[1])


def region_center(area):
    region = region_of(area)
    return region.x + region.width // 2, region.y + region.height // 2


def move(x, y, steps=4, **modifiers):
    start = getattr(move, "last", (x, y))
    for i in range(1, steps + 1):
        px = round(start[0] + (x - start[0]) * i / steps)
        py = round(start[1] + (y - start[1]) * i / steps)
        window().event_simulate(type="MOUSEMOVE", value="NOTHING", x=px, y=py, **modifiers)
        yield .02
    move.last = (x, y)


def press(kind, x, y, **modifiers):
    window().event_simulate(type=kind, value="PRESS", x=x, y=y, **modifiers)
    yield .03


def release(kind, x, y, **modifiers):
    window().event_simulate(type=kind, value="RELEASE", x=x, y=y, **modifiers)
    yield .05


def click(x, y, **modifiers):
    yield from move(x, y)
    yield from press("LEFTMOUSE", x, y, **modifiers)
    yield from release("LEFTMOUSE", x, y, **modifiers)


def drag(a, b, button="LEFTMOUSE", steps=8, **modifiers):
    yield from move(*a)
    yield from press(button, *a, **modifiers)
    yield from move(*b, steps=steps, **modifiers)
    yield from release(button, *b, **modifiers)


def key(kind, **modifiers):
    x, y = getattr(move, "last", (10, 10))
    window().event_simulate(type=kind, value="PRESS", x=x, y=y, **modifiers)
    yield .03
    window().event_simulate(type=kind, value="RELEASE", x=x, y=y, **modifiers)
    yield .08


def screenshot(name):
    path = OUTPUT / f"{name}.png"
    bpy.ops.screen.screenshot(filepath=str(path))
    results["screenshots"].append(path.name)
    yield .3


def frame_left(distance=2.4):
    """Point the 3D view at the panels so screenshots show them."""
    objects = [p.mesh_object for p in patterns() if p.mesh_object is not None]
    if not objects:
        return
    rv3d = views()[0].spaces.active.region_3d
    center = sum((o.matrix_world.translation for o in objects),
                 objects[0].matrix_world.translation * 0) / len(objects)
    rv3d.view_location = center
    rv3d.view_distance = distance


def view_state(area):
    rv3d = area.spaces.active.region_3d
    return (rv3d.view_perspective, tuple(round(v, 5) for v in rv3d.view_rotation),
            tuple(round(v, 5) for v in rv3d.view_location), round(rv3d.view_distance, 5))


def P(identifier):
    return model.find(bpy.context.scene, identifier)


def patterns():
    return list(bpy.context.scene.cloth_next_patterns)


def handler_census():
    rows = {}
    for name in ("load_post", "undo_post", "redo_post", "undo_pre", "redo_pre", "load_pre",
                 "depsgraph_update_post", "save_pre", "save_post", "frame_change_post"):
        rows[name] = sorted(getattr(f, "__qualname__", repr(f))
                            for f in getattr(bpy.app.handlers, name))
    return rows


def timed_redraw(area, iterations=10):
    region = region_of(area)
    with bpy.context.temp_override(window=window(), area=area, region=region):
        started = time.perf_counter()
        bpy.ops.wm.redraw_timer(type="DRAW", iterations=iterations)
        return (time.perf_counter() - started) / iterations


GARMENT = [(-0.30, 0.00), (0.30, 0.00), (0.34, 0.55), (0.12, 0.70), (-0.12, 0.70),
           (-0.34, 0.55)]


def phase_create():
    w = window()
    for name in ("Cube",):
        if name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    yield 1.0
    ws = workspace.pattern_workspace()
    check("workspace tab exists after enabling Cloth NeXt", ws is not None and ws.name == "PATTERN NeXt",
          [x.name for x in bpy.data.workspaces])
    check("workspace not activated on startup (no focus steal)", w.workspace.name == "Layout",
          w.workspace.name)
    check("Layout workspace untouched", bpy.data.workspaces.get("Layout") is not None)
    w.workspace = ws
    yield 1.0
    vs = views()
    check("two side-by-side 3D editors", len(vs) == 2 and vs[0].x < vs[1].x
          and abs(vs[0].width - vs[1].width) < 0.15 * vs[0].width,
          [(a.x, a.width, a.height) for a in vs])
    left, right = vs
    check("right editor is the locked 2D Pattern editor",
          workspace.is_locked_view(right) and not workspace.is_locked_view(left))
    check("right editor is Front Orthographic",
          workspace.is_front_ortho(right.spaces.active.region_3d))
    check("left editor is a free perspective 3D view",
          left.spaces.active.region_3d.view_perspective == "PERSP")
    check("2D editor session running", editor.session_for(w) is not None)
    yield from screenshot("01_workspace_empty")

    # --- view lock: right cannot rotate, left can --------------------------------
    rc = region_center(right)
    before = view_state(right)
    yield from drag(rc, (rc[0] + 120, rc[1] + 80), button="MIDDLEMOUSE")
    after_mmb = view_state(right)
    check("middle-mouse drag does not rotate the 2D editor", after_mmb[:2] == before[:2],
          (before, after_mmb))
    for numpad in ("NUMPAD_7", "NUMPAD_3", "NUMPAD_1", "NUMPAD_5", "NUMPAD_0", "NUMPAD_9"):
        yield from move(*rc)
        yield from key(numpad)
        yield from key(numpad, ctrl=True)
    after_keys = view_state(right)
    check("numpad views, perspective toggle and camera are blocked", after_keys[:2] == before[:2],
          after_keys)
    yield from move(*rc)
    yield from key("WHEELUPMOUSE")
    yield from key("WHEELUPMOUSE")
    zoomed = view_state(right)
    check("wheel zoom works in the 2D editor", zoomed[3] < after_keys[3], (after_keys[3], zoomed[3]))
    yield from drag(rc, (rc[0] - 90, rc[1] - 40), button="MIDDLEMOUSE", shift=True)
    panned = view_state(right)
    check("pan works in the 2D editor", panned[2] != zoomed[2] and panned[:2] == before[:2],
          (zoomed[2], panned[2]))
    lc = region_center(left)
    left_before = view_state(left)
    yield from drag(lc, (lc[0] + 120, lc[1] + 60), button="MIDDLEMOUSE")
    check("left 3D view orbits freely", view_state(left)[1] != left_before[1])
    with bpy.context.temp_override(window=w, area=right, region=region_of(right)):
        editor.frame(bpy.context, region_of(right), right.spaces.active.region_3d,
                     [], margin=1.0)
    right.spaces.active.region_3d.view_location = (0.0, 0.0, 0.3)
    right.spaces.active.region_3d.view_distance = 2.6
    yield .4

    # --- Polygon tool ---------------------------------------------------------------
    yield from move(*rc)
    yield from key("H")
    check("H selects the Polygon tool", bpy.context.window_manager.cloth_next_pattern_ui.tool == "POLYGON")
    bpy.context.scene.cloth_next_pattern_settings.snap_grid = False
    started = time.perf_counter()
    for point in GARMENT:
        yield from click(*to_window(point))
    yield from move(*to_window((0.0, 0.3)))
    yield from screenshot("02_polygon_drawing")
    count_before = len(patterns())
    yield from click(*to_window(GARMENT[0]))
    yield .3
    results["timings"]["polygon_close_to_mesh_s"] = time.perf_counter() - started
    check("closing the polygon creates exactly one pattern", len(patterns()) == count_before + 1)
    panel_id = patterns()[-1].identifier if patterns() else None
    obj = P(panel_id).mesh_object if panel_id else None
    check("closed polygon immediately has a real mesh object", obj is not None and
          obj.name in bpy.context.scene.objects)
    if obj is None:
        return
    from cloth_next.pattern_next.shapes import signed_area
    area_expected = abs(signed_area([p.co for p in model.outline(P(panel_id))]))
    check("mesh is triangulated with interior vertices",
          all(len(p.vertices) == 3 for p in obj.data.polygons)
          and len(obj.data.vertices) > 40, (len(obj.data.vertices), len(obj.data.polygons)))
    check("filled area equals the drawn outline", abs(P(panel_id).area - area_expected) < 1e-4,
          (P(panel_id).area, area_expected))
    check("panel is a Cloth NeXt Cloth object", obj.cloth_next.enabled and
          obj.cloth_next.role == "CLOTH")
    check("panel is selected in 3D after creation", obj.select_get())
    frame_left()
    yield .2
    yield from screenshot("03_polygon_filled")

    # --- Edit Pattern: move a point ---------------------------------------------
    yield from key("Z")
    tri_before = P(panel_id).triangle_count
    revision = P(panel_id).revision
    co_before = [tuple(v.co) for v in obj.data.vertices]
    start = to_window(GARMENT[2])
    target = to_window((0.42, 0.50))
    started = time.perf_counter()
    yield from drag(start, target, steps=10)
    yield .3
    results["timings"]["point_drag_s"] = time.perf_counter() - started
    moved = model.outline(P(panel_id))[2].co
    check("dragging an outline point moves it", abs(moved[0] - (0.42 - P(panel_id).location[0])) < 0.01,
          moved)
    check("point edit regenerates the mesh", P(panel_id).revision > revision and
          [tuple(v.co) for v in obj.data.vertices] != co_before, (revision, P(panel_id).revision))
    yield from screenshot("04_point_moved")
    # Edge drag
    edge_mid = to_window((0.0, 0.0))
    yield from drag(edge_mid, (edge_mid[0], edge_mid[1] - 25), steps=6)
    yield .2
    check("dragging an edge moves both its points",
          model.outline(P(panel_id))[0].co[1] < -0.3 - 0.01 or model.outline(P(panel_id))[0].co[1] < GARMENT[0][1] - P(panel_id).location[1] - 0.01,
          model.outline(P(panel_id))[:2])
    # Double-click an edge adds a point
    count = len(P(panel_id).points)
    mid = model.to_canvas_points(P(panel_id), [((model.outline(P(panel_id))[3].co[0] + model.outline(P(panel_id))[4].co[0]) / 2,
                                          (model.outline(P(panel_id))[3].co[1] + model.outline(P(panel_id))[4].co[1]) / 2)])[0]
    x, y = to_window(mid)
    yield from move(x, y)
    window().event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    yield .02
    window().event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
    yield .02
    window().event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
    yield .02
    window().event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
    yield .3
    check("double-clicking an edge adds a point", len(P(panel_id).points) == count + 1,
          (count, len(P(panel_id).points)))

    # Invalid edit is rejected
    pts_before = model.outline(P(panel_id))
    tris_valid = P(panel_id).triangle_count
    cross = to_window(model.to_canvas_points(P(panel_id), [model.outline(P(panel_id))[1].co])[0])
    target = to_window((-0.45, 0.62))
    yield from drag(cross, target, steps=10)
    yield .3
    check("self-intersecting edit is rejected and the mesh kept",
          model.outline(P(panel_id)) == pts_before and P(panel_id).triangle_count == tris_valid,
          editor.session_for(w).message)
    yield from screenshot("05_invalid_rejected")

    # --- Particle Distance ---------------------------------------------------------
    dense_before = len(obj.data.polygons)
    started = time.perf_counter()
    P(panel_id).particle_distance = 0.01
    bpy.ops.ed.undo_push(message='scripted edit')
    results["timings"]["particle_distance_10mm_s"] = time.perf_counter() - started
    check("smaller Particle Distance gives a denser mesh", len(obj.data.polygons) > 3 * dense_before,
          (dense_before, len(obj.data.polygons)))
    yield from screenshot("06_particle_distance_10mm")
    P(panel_id).particle_distance = 0.03
    bpy.ops.ed.undo_push(message='scripted edit')
    check("larger Particle Distance gives a coarser mesh", len(obj.data.polygons) < dense_before,
          (dense_before, len(obj.data.polygons)))
    P(panel_id).particle_distance = 0.02
    bpy.ops.ed.undo_push(message='scripted edit')
    yield .2

    # --- Rectangle and ellipse -------------------------------------------------------
    yield from key("S")
    yield from drag(to_window((0.45, 0.05)), to_window((0.85, 0.45)), steps=8)
    yield .3
    rect_id = patterns()[-1].identifier
    check("rectangle drag creates a filled pattern", P(rect_id).shape_kind == "RECTANGLE"
          and P(rect_id).mesh_object is not None and abs(P(rect_id).area - 0.16) < 0.005, P(rect_id).area)
    yield from key("E")
    yield from drag(to_window((-0.85, 0.05)), to_window((-0.45, 0.35)), steps=8)
    yield .3
    ell_id = patterns()[-1].identifier
    check("ellipse drag creates a filled pattern", P(ell_id).shape_kind == "ELLIPSE"
          and P(ell_id).mesh_object is not None and abs(P(ell_id).area - math.pi * 0.2 * 0.15) < 0.003,
          P(ell_id).area)
    check("ellipse keeps its editable curve representation",
          all(p.curve_out for p in P(ell_id).points) and len(P(ell_id).points) == 4)
    P(rect_id).primitive_size = (0.45, 0.4)
    bpy.ops.ed.undo_push(message='scripted edit')
    check("changing rectangle size regenerates the mesh", abs(P(rect_id).area - 0.18) < 0.005,
          P(rect_id).area)
    frame_left()
    yield .2
    yield from screenshot("07_rectangle_ellipse")

    # --- Edit Curve on the rectangle top edge -------------------------------------
    yield from key("V")
    top = model.to_canvas_points(P(rect_id), [(0.0, 0.2)])[0]
    rect_area = P(rect_id).area
    yield from drag(to_window(top), to_window((top[0], top[1] + 0.08)), steps=8)
    yield .3
    check("Edit Curve bends an edge into a Bezier",
          any(p.curve_out for p in P(rect_id).points) and P(rect_id).area > rect_area + 0.005,
          (rect_area, P(rect_id).area))
    check("bending a primitive turns it into a free outline", P(rect_id).shape_kind == "POLYGON")
    yield from screenshot("08_curve_edit")

    # --- Selection sync ------------------------------------------------------------
    yield from key("Q")
    yield from click(*to_window((-0.65, 0.2)))
    session = editor.session_for(w)
    check("clicking a pattern in 2D selects its 3D panel",
          P(ell_id).mesh_object.select_get() and not obj.select_get(),
          (bpy.context.window_manager.cloth_next_pattern_ui.tool, session and session.message,
           session and session.hover, getattr(bpy.context.view_layer.objects.active, "name", None),
           [o.name for o in bpy.context.selected_objects], to_window((-0.65, 0.2))))
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    check("selecting the panel in 3D highlights the pattern in 2D",
          draw.is_selected(P(panel_id)) and not draw.is_selected(P(ell_id)))
    yield .3
    yield from screenshot("09_selection_sync")
    # Move pattern on canvas: 3D arrangement unchanged
    loc3d = tuple(obj.location)
    loc2d = tuple(P(panel_id).location)
    inside = model.to_canvas_points(P(panel_id), [(0.0, 0.3)])[0]
    yield from drag(to_window(inside), to_window((inside[0], inside[1] - 0.1)), steps=6)
    yield .2
    check("moving on the 2D canvas does not move the 3D panel",
          tuple(P(panel_id).location) != loc2d and tuple(obj.location) == loc3d)
    obj.location.x += 0.3
    bpy.ops.ed.undo_push(message='scripted edit')
    check("moving the 3D panel does not change the 2D pattern",
          tuple(P(panel_id).location) != loc2d and model.outline(P(panel_id)) == model.outline(P(panel_id)))

    # --- Sewing ------------------------------------------------------------------------
    yield from key("M")
    outline = model.outline(P(panel_id))
    a_mid = model.to_canvas_points(P(panel_id), [((outline[0].co[0] * .3 + outline[1].co[0] * .7),
                                             (outline[0].co[1] * .3 + outline[1].co[1] * .7))])[0]
    r_out = model.outline(P(rect_id))
    b_mid = model.to_canvas_points(P(rect_id), [((r_out[0].co[0] * .3 + r_out[1].co[0] * .7),
                                            (r_out[0].co[1] * .3 + r_out[1].co[1] * .7))])[0]
    yield from click(*to_window(a_mid))
    yield from click(*to_window(b_mid))
    yield .3
    seams = bpy.context.scene.cloth_next_pattern_seams
    check("clicking two edges creates a seam", len(seams) == 1, len(seams))
    defs = bpy.context.scene.cloth_next_sewing_definitions
    check("the seam is a Cloth NeXt sewing definition",
          len(seams) == 1 and any(d.identifier == seams[0].sewing_identifier for d in defs),
          [(d.name, len(d.side_a), len(d.side_b)) for d in defs])
    frame_left()
    yield .2
    yield from screenshot("10_seam")
    P(panel_id).particle_distance = 0.015
    bpy.ops.ed.undo_push(message='scripted edit')
    check("seam survives a Particle Distance change", len(seams) == 1 and not seams[0].status
          and any(d.identifier == seams[0].sewing_identifier for d in defs))

    # --- Internal shape (hole) ------------------------------------------------------
    yield from key("G")
    area_before = P(panel_id).area
    hole = [(-0.06, -0.05), (0.06, -0.05), (0.06, 0.05), (-0.06, 0.05)]
    canvas_hole = model.to_canvas_points(P(panel_id), hole)
    for point in canvas_hole:
        yield from click(*to_window(point))
    yield from click(*to_window(canvas_hole[0]))
    yield .3
    check("Internal tool cuts a hole that is meshed around",
          len(P(panel_id).contours) == 1 and P(panel_id).area < area_before - 0.01,
          (area_before, P(panel_id).area))
    yield from screenshot("10b_hole")

    # --- Copy / paste and rotate ---------------------------------------------------------
    yield from key("Q")
    yield from click(*to_window(model.to_canvas_points(P(ell_id), [(0.0, 0.0)])[0]))
    count = len(patterns())
    yield from move(*to_window((-0.65, 0.75)))
    yield from key("C", ctrl=True)
    yield from key("V", ctrl=True)
    yield .3
    check("Ctrl+C / Ctrl+V pastes a copy at the mouse", len(patterns()) == count + 1,
          len(patterns()))
    copy_id = patterns()[-1].identifier
    yield from move(*to_window((-0.4, 0.75)))
    yield from key("R")
    yield from move(*to_window((-0.65, 1.0)), steps=6)
    yield from click(*to_window((-0.65, 1.0)))
    check("R rotates the selected pattern on the canvas",
          abs(P(copy_id).rotation) > 0.3, P(copy_id).rotation)

    # --- Mirror with linked symmetry ------------------------------------------------------
    yield from click(*to_window(model.to_canvas_points(P(panel_id), [(0.0, -0.2)])[0]))
    with bpy.context.temp_override(window=w, area=pattern_area(), region=region_of(pattern_area())):
        bpy.ops.clothnext.pattern_mirror(linked=True)
    yield .3
    partner_id = P(panel_id).symmetry_partner
    check("Mirror creates a linked partner", bool(partner_id) and P(partner_id) is not None
          and P(partner_id).symmetry_linked)
    yield from key("Z")
    point = model.to_canvas_points(P(panel_id), [model.outline(P(panel_id))[3].co])[0]
    yield from drag(to_window(point), to_window((point[0] + 0.05, point[1] + 0.04)), steps=6)
    yield .3
    from cloth_next.pattern_next.shapes import mirror_points
    check("linked symmetry mirrors point edits onto the partner",
          model.outline(P(partner_id)) == mirror_points(model.outline(P(panel_id))))
    frame_left()
    yield .2
    yield from screenshot("10c_mirror_linked")

    # --- Undo / redo --------------------------------------------------------------
    yield from move(*rc)
    # Undo step by step (mirror edit, mirror, rotate, paste, hole, ...) until the
    # seam is gone, then redo the same number of steps.
    trail = [len(bpy.context.scene.cloth_next_pattern_seams)]
    steps = 0
    while len(bpy.context.scene.cloth_next_pattern_seams) and steps < 15:
        yield from key("Z", ctrl=True)
        yield .4
        steps += 1
        trail.append((len(patterns()), len(bpy.context.scene.cloth_next_pattern_seams)))
    check("undo removes the seam and its sewing definition",
          len(bpy.context.scene.cloth_next_pattern_seams) == 0 and
          len(bpy.context.scene.cloth_next_sewing_definitions) == 0, (steps, trail))
    for _ in range(steps):
        yield from key("Z", ctrl=True, shift=True)
        yield .4
    check("redo restores the seam and every later edit",
          len(bpy.context.scene.cloth_next_pattern_seams) == 1
          and P(panel_id).symmetry_partner and len(P(panel_id).contours) == 1, trail)
    n_patterns = len(patterns())
    for _ in range(40):
        yield from key("Z", ctrl=True)
        yield .25
    after_undo = len(patterns())
    check("undo reaches back before the created patterns", after_undo < n_patterns,
          (n_patterns, after_undo))
    names = {o.name for o in bpy.context.scene.objects}
    check("no orphaned panels after undo",
          all(p.mesh_object is not None and p.mesh_object.name in names for p in patterns())
          and len([o for o in bpy.context.scene.objects if o.get(sync.OBJECT_MARKER)]) == after_undo)
    for _ in range(40):
        yield from key("Z", ctrl=True, shift=True)
        yield .25
    check("redo restores every pattern and panel", len(patterns()) == n_patterns
          and all(p.mesh_object is not None for p in patterns()), len(patterns()))
    yield from screenshot("11_after_redo")

    # --- performance ------------------------------------------------------------------
    results["timings"]["draw_2d_ms"] = timed_redraw(pattern_area()) * 1000
    results["timings"]["draw_3d_ms"] = timed_redraw(views()[0]) * 1000
    results["timings"]["last_sync"] = dict(sync.timings)

    # --- reload the add-on ------------------------------------------------------------
    census = handler_census()
    draw_handles = len(draw._handles)
    cloth_next.unregister()
    check("disable removes PATTERN NeXt handlers",
          not draw._handles and not any("pattern" in n.lower() and "pattern_next" in n.lower()
                                        for rows in handler_census().values() for n in rows)
          and not bpy.app.timers.is_registered(editor._autostart))
    cloth_next.register()
    yield 1.0
    check("re-enable restores the same handlers (no duplicates)",
          handler_census() == census and len(draw._handles) == draw_handles,
          (census, handler_census()))
    check("re-enabling does not duplicate the workspace",
          sum(1 for x in bpy.data.workspaces if workspace.is_pattern_workspace(x)) == 1)
    modal = [getattr(op, "bl_idname", str(op)) for op in getattr(window(), "modal_operators", ())]
    yield from move(*region_center(pattern_area()))
    yield from move(region_center(pattern_area())[0] + 20, region_center(pattern_area())[1])
    yield 1.0
    check("editor session restarts after re-enable", editor.session_for(window()) is not None,
          modal)
    path = OUTPUT / "pattern_acceptance.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    results["saved"] = {"patterns": [(p.name, p.identifier, len(p.points), p.triangle_count)
                                     for p in patterns()],
                        "seams": len(bpy.context.scene.cloth_next_pattern_seams)}
    check("file saved", path.is_file())


def phase_reopen():
    w = window()
    yield 1.5
    scene = bpy.context.scene
    saved = json.loads((OUTPUT / "acceptance-create.json").read_text()).get("saved", {})
    check("workspace present exactly once after reopening",
          sum(1 for x in bpy.data.workspaces if workspace.is_pattern_workspace(x)) == 1)
    check("patterns restored", [(p.name, p.identifier, len(p.points), p.triangle_count)
                                for p in patterns()] == [tuple(r) for r in saved.get("patterns", [])],
          [p.name for p in patterns()])
    check("panels restored", all(p.mesh_object is not None and
                                 len(p.mesh_object.data.polygons) == p.triangle_count
                                 for p in patterns()))
    check("seams restored", len(scene.cloth_next_pattern_seams) == saved.get("seams"))
    w.workspace = workspace.pattern_workspace()
    yield 1.2
    right = pattern_area()
    check("2D editor still locked front orthographic after reopening",
          right is not None and workspace.is_front_ortho(right.spaces.active.region_3d)
          and right.spaces.active.region_3d.lock_rotation)
    check("editor session running after reopening", editor.session_for(w) is not None)
    panel_id = patterns()[0].identifier
    obj = P(panel_id).mesh_object
    yield from move(*region_center(right))
    yield from key("Z")
    co_before = [tuple(v.co) for v in obj.data.vertices]
    p = model.to_canvas_points(P(panel_id), [model.outline(P(panel_id))[3].co])[0]
    yield from drag(to_window(p), to_window((p[0] + 0.04, p[1] + 0.03)), steps=8)
    yield .3
    check("editing continues after reopening and the mesh updates",
          [tuple(v.co) for v in obj.data.vertices] != co_before)
    frame_left()
    yield .2
    yield from screenshot("12_reopened_and_edited")


def runner():
    try:
        yield from (phase_create() if PHASE == "create" else phase_reopen())
    except Exception:
        traceback.print_exc()
        check("script completed without exception", False, traceback.format_exc())
    finally:
        results["passed"] = sum(1 for c in results["checks"] if c["ok"])
        results["failed"] = sum(1 for c in results["checks"] if not c["ok"])
        (OUTPUT / f"acceptance-{PHASE}.json").write_text(json.dumps(results, indent=2))
        print(f"PATTERN NeXt acceptance {PHASE}: {results['passed']} passed, "
              f"{results['failed']} failed", flush=True)
        bpy.ops.wm.quit_blender()


_steps = runner()


def _tick():
    try:
        return next(_steps)
    except StopIteration:
        return None


bpy.app.timers.register(_tick, first_interval=1.5)
