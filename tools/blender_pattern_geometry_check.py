# SPDX-License-Identifier: GPL-3.0-or-later
"""PATTERN NeXt geometry validation and benchmarks in real Blender.

    blender --background --factory-startup \
        --python tools/blender_pattern_geometry_check.py -- <output.json>

Meshes the acceptance shapes with Blender's own constrained Delaunay
(``mathutils.geometry.delaunay_2d_cdt``) through the add-on's mesher,
checks each result independently (``cloth_next.pattern_next.quality``)
and benchmarks meshing, mesh writing and multi-pattern scenes.
"""
import json
import math
import random
import sys
import time
from dataclasses import replace
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUTPUT = Path(argv[0]) if argv else ROOT / "docs/validation/pattern_next/geometry.json"

import cloth_next  # noqa: E402
cloth_next.register()
from cloth_next.blender.pattern_next import model, sync  # noqa: E402
from cloth_next.pattern_next import editing, quality  # noqa: E402
from cloth_next.pattern_next.shapes import (Contour, ContourPoint, PatternShape,  # noqa: E402
                                            ellipse_points, rectangle_points)
from cloth_next.pattern_next.validation import PatternError, validate_ring  # noqa: E402
from cloth_next.pattern_next.curves import display_polyline  # noqa: E402

TOL = 0.001
report = {"blender": bpy.app.version_string, "cases": [], "benchmarks": {}}


def poly(points, curved=()):
    result = []
    for i, co in enumerate(points):
        result.append(ContourPoint(i + 1, co))
    for index, handles in curved:
        a = result[index]
        b = result[(index + 1) % len(result)]
        result[index] = replace(a, curve_out=True, handle_out=handles[0])
        result[(index + 1) % len(result)] = replace(b, handle_in=handles[1])
    return tuple(result)


def shape(points, spacing=0.02, holes=(), lines=()):
    return PatternShape(Contour(points), tuple(Contour(h) for h in holes),
                        tuple(Contour(l, False) for l in lines), spacing, TOL)


def run(name, s, note=""):
    started = time.perf_counter()
    try:
        result = sync.mesh(s)
    except PatternError as exc:
        report["cases"].append({"name": name, "ok": False, "error": str(exc)})
        print("FAIL", name, exc, flush=True)
        return None
    elapsed = time.perf_counter() - started
    row = quality.check(result, s)
    row.update(name=name, note=note, spacing=s.spacing, mesh_ms=elapsed * 1000,
               ok=quality.passed(row, TOL))
    report["cases"].append(row)
    print(("PASS " if row["ok"] else "FAIL ") + name,
          {k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()
           if k in ("vertices", "triangles", "area_error_relative", "min_angle",
                    "boundary_deviation", "mesh_ms")}, flush=True)
    return result


# 1-3 basic shapes
run("1 triangle", shape(poly([(0, 0), (0.5, 0), (0.2, 0.4)])))
run("2 rectangle", shape(rectangle_points(0.6, 0.4, range(1, 5))))
run("3 concave L", shape(poly([(0, 0), (0.5, 0), (0.5, 0.12), (0.12, 0.12), (0.12, 0.6),
                               (0, 0.6)])))
# 4 complex concave polygon: a comb / star
star = []
for k in range(14):
    r = 0.35 if k % 2 == 0 else 0.13
    a = math.pi * 2 * k / 14
    star.append((r * math.cos(a), r * math.sin(a)))
comb = [(0, 0), (0.8, 0), (0.8, 0.3)]
for k in range(5):
    x = 0.8 - 0.16 * k
    comb += [(x - 0.04, 0.3), (x - 0.04, 0.12), (x - 0.12, 0.12), (x - 0.12, 0.3)]
comb += [(0, 0.3)]
run("4a complex concave star", shape(poly(star)))
run("4b complex concave comb", shape(poly(comb[:-1] + [(0.0, 0.3)])), "5 slots")
# 5 ellipse
run("5 ellipse", shape(ellipse_points(0.3, 0.18, range(1, 5))))
# 6 Bezier outline: sleeve with a curved cap and straight sides (mixed)
sleeve = poly([(-0.25, 0), (0.25, 0), (0.22, 0.38), (-0.22, 0.38)],
              curved=[(2, ((-0.05, 0.18), (0.05, 0.18)))])
run("6 bezier sleeve cap (mixed straight/curved)", shape(sleeve))
bodice = poly([(-0.25, 0), (0.25, 0), (0.27, 0.42), (0.10, 0.60), (-0.10, 0.60), (-0.27, 0.42)],
              curved=[(1, ((0.03, 0.15), (0.02, -0.15))), (2, ((-0.03, 0.08), (0.08, -0.02))),
                      (4, ((-0.08, -0.02), (0.03, 0.08))), (5, ((-0.02, -0.15), (-0.03, 0.15)))])
run("6b curved bodice", shape(bodice))
# 7 narrow sections: dumbbell with a 1.5 cm neck at 20 mm Particle Distance
run("7 narrow neck 15 mm", shape(poly([(0, 0), (0.3, 0), (0.3, 0.2), (0.17, 0.2), (0.17, 0.215),
                                       (0.3, 0.215), (0.3, 0.42), (0, 0.42), (0, 0.215),
                                       (0.13, 0.215), (0.13, 0.2), (0, 0.2)])))
run("7b narrow sliver triangle", shape(poly([(0, 0), (0.6, 0), (0.6, 0.012)])))
# 8 near-collinear points
near = poly([(0, 0), (0.2, 0.00001), (0.4, -0.00001), (0.6, 0), (0.6, 0.3), (0.3, 0.30002),
             (0, 0.3)])
run("8 near-collinear points", shape(near))
# 9 repeated point movement
rng = random.Random(7)
points = poly([(math.cos(a) * 0.3, math.sin(a) * 0.25)
               for a in (2 * math.pi * k / 10 for k in range(10))])
accepted = rejected = 0
for step in range(60):
    i = rng.randrange(len(points))
    candidate = editing.move_point(points, i, (points[i].co[0] + rng.uniform(-0.06, 0.06),
                                               points[i].co[1] + rng.uniform(-0.06, 0.06)))
    try:
        validate_ring(display_polyline(Contour(candidate), TOL))
    except PatternError:
        rejected += 1
        continue
    points = candidate
    accepted += 1
    result = sync.mesh(shape(points))
    row = quality.check(result, shape(points))
    if not quality.passed(row, TOL):
        report["cases"].append(dict(row, name=f"9 movement step {step}", ok=False))
        print("FAIL movement step", step, flush=True)
run("9 after repeated point movement", shape(points),
    f"60 random moves: {accepted} accepted, {rejected} rejected (self-intersection)")
# 10 densities
for spacing in (0.005, 0.01, 0.02, 0.04):
    run(f"10 bodice at {spacing * 1000:.0f} mm", shape(bodice, spacing))
# extras: hole and internal line
hole = tuple(ContourPoint(100 + i, co) for i, co in enumerate(
    [(-0.08, 0.2), (0.08, 0.2), (0.08, 0.3), (-0.08, 0.3)]))
line = (ContourPoint(200, (-0.15, 0.1)), ContourPoint(201, (0.15, 0.45)))
run("11 hole", shape(bodice, holes=(hole,)))
run("12 internal line", shape(bodice, lines=(line,)))

# -- invalid shapes are rejected -----------------------------------------------
invalid = {
    "open contour": PatternShape(Contour(rectangle_points(0.3, 0.3, range(4)), False)),
    "self intersection": shape(poly([(0, 0), (0.4, 0.4), (0.4, 0), (0, 0.25)])),
    "duplicate points": shape(poly([(0, 0), (0.4, 0), (0.4, 0), (0.2, 0.3)])),
    "zero area": shape(poly([(0, 0), (0.4, 0), (0.8, 0)])),
    "hole outside": shape(rectangle_points(0.3, 0.3, range(4)), holes=(tuple(
        ContourPoint(10 + i, (x + 1, y)) for i, (x, y) in enumerate(
            [(0, 0), (0.1, 0), (0.1, 0.1)])),)),
}
report["invalid"] = {}
for name, s in invalid.items():
    try:
        sync.mesh(s)
        report["invalid"][name] = "NOT REJECTED"
        print("FAIL invalid accepted:", name, flush=True)
    except PatternError as exc:
        report["invalid"][name] = f"rejected ({exc.code}): {exc}"
        print("PASS rejected", name, "->", exc.code, flush=True)

# -- benchmarks with real Blender mesh objects ----------------------------------------
scene = bpy.context.scene


def make(name, points, spacing):
    p = model.new_pattern(scene, name, points, particle_distance=spacing)
    started = time.perf_counter()
    sync.rebuild(scene, p, force=True)
    total = time.perf_counter() - started
    return p.identifier, total, dict(sync.timings)


bench = report["benchmarks"]
ident, total, timing = make("Small", model.translate(rectangle_points(0.3, 0.2, range(4)), (0, 0)), 0.02)
bench["small 30x20 cm @20 mm"] = {"total_ms": total * 1000, "write_ms": timing["write_mesh"] * 1000,
                                  "triangles": model.find(scene, ident).triangle_count}
big = [replace(p, co=(p.co[0] * 2.2, p.co[1] * 2.2), handle_in=(p.handle_in[0] * 2.2, p.handle_in[1] * 2.2),
               handle_out=(p.handle_out[0] * 2.2, p.handle_out[1] * 2.2)) for p in bodice]
ident, total, timing = make("Complex", tuple(big), 0.01)
bench["complex curved bodice ~1 m2 @10 mm"] = {
    "total_ms": total * 1000, "mesh_ms": timing["mesh"] * 1000,
    "write_ms": timing["write_mesh"] * 1000, "triangles": model.find(scene, ident).triangle_count}
wobbly = [ContourPoint(0, ((0.4 + 0.02 * math.sin(9 * a)) * math.cos(a),
                           (0.4 + 0.02 * math.sin(9 * a)) * math.sin(a)))
          for a in (2 * math.pi * k / 400 for k in range(400))]
ident, total, timing = make("Outline400", tuple(wobbly), 0.01)
bench["400 outline points @10 mm"] = {"total_ms": total * 1000, "mesh_ms": timing["mesh"] * 1000,
                                      "write_ms": timing["write_mesh"] * 1000,
                                      "triangles": model.find(scene, ident).triangle_count}
for spacing in (0.005, 0.01, 0.02, 0.04):
    ident, total, timing = make(f"Density{spacing}", tuple(big), spacing)
    bench[f"bodice @{spacing * 1000:.0f} mm"] = {
        "total_ms": total * 1000, "mesh_ms": timing["mesh"] * 1000,
        "write_ms": timing["write_mesh"] * 1000,
        "triangles": model.find(scene, ident).triangle_count}
# multiple patterns: editing one must not rebuild the others
ids = []
for k in range(20):
    ident, _total, _timing = make(f"Multi{k}", model.translate(
        rectangle_points(0.25, 0.35, range(4)), (k * 0.3, 2.0)), 0.015)
    ids.append(ident)
before = {i: model.find(scene, i).revision for i in ids}
target = model.find(scene, ids[7])
points = editing.move_point(model.outline(target), 2, (0.2, 0.25))
started = time.perf_counter()
ok, message = sync.commit(scene, target, points)
edit_ms = (time.perf_counter() - started) * 1000
after = {i: model.find(scene, i).revision for i in ids}
changed = [i for i in ids if after[i] != before[i]]
bench["20 patterns, edit one"] = {"edit_ms": edit_ms, "ok": ok,
                                  "patterns_rebuilt": len(changed),
                                  "only_edited_rebuilt": changed == [ids[7]]}
print("BENCH", json.dumps(bench, indent=1), flush=True)

# -- adapter boundary: snapshot agrees with the Blender objects and sewing --------------
from cloth_next.blender.pattern_next import adapter  # noqa: E402
import uuid  # noqa: E402
a, b = model.find(scene, ids[0]), model.find(scene, ids[1])
seam = scene.cloth_next_pattern_seams.add()
seam.identifier, seam.name = uuid.uuid4().hex, "adapter check"
oa, ob = model.outline(a), model.outline(b)
seam.a_pattern, seam.a_start, seam.a_end = a.identifier, oa[1].uid, oa[2].uid
seam.b_pattern, seam.b_start, seam.b_end = b.identifier, ob[3].uid, ob[0].uid
sync.derive_seam(scene, seam)
garment = adapter.snapshot(scene)
panel_ok = all(
    len(p.rest_vertices) == len(bpy.data.objects[p.object_name].data.vertices)
    and abs(p.rest_vertices[:, 0].sum() - sum(v.co.x for v in bpy.data.objects[p.object_name].data.vertices)) < 1e-3
    for p in garment.panels)
row = next(d for d in scene.cloth_next_sewing_definitions if d.identifier == seam.sewing_identifier)
seam_ok = (len(garment.seams) == 1
           and list(garment.seams[0].source_vertices) == [r.index for r in row.side_a]
           and list(garment.seams[0].target_vertices) == [r.index for r in row.side_b])
report["adapter"] = {"panels": len(garment.panels), "seams": len(garment.seams),
                     "panels_match_objects": panel_ok, "seam_matches_sewing_definition": seam_ok}
print(("PASS" if panel_ok and seam_ok else "FAIL") + " adapter snapshot", report["adapter"], flush=True)
report["passed"] = sum(1 for c in report["cases"] if c.get("ok"))
report["failed"] = sum(1 for c in report["cases"] if not c.get("ok"))
report["invalid_all_rejected"] = all(v != "NOT REJECTED" for v in report["invalid"].values())
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps(report, indent=2))
print(f"PATTERN NeXt geometry: {report['passed']} passed, {report['failed']} failed, "
      f"invalid rejected: {report['invalid_all_rejected']}", flush=True)
