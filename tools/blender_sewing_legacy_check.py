# SPDX-License-Identifier: GPL-3.0-or-later
"""Sewing data compatibility check across add-on versions (headless).

    # 1. with an older checkout: author seams and record their export
    blender -b --factory-startup --python tools/blender_sewing_legacy_check.py -- \
        write <old-repo> <out-dir>
    # 2. with this checkout: load the file and compare the export
    blender -b --factory-startup --python tools/blender_sewing_legacy_check.py -- \
        read <this-repo> <out-dir>
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
mode, repo, output = argv[0], Path(argv[1]), Path(argv[2])
sys.dont_write_bytecode = True
sys.path.insert(0, str(repo))
import cloth_next  # noqa: E402
cloth_next.register()
from cloth_next.blender import sewing  # noqa: E402
from cloth_next.attachments import wire_entry  # noqa: E402

output.mkdir(parents=True, exist_ok=True)
blend = output / "legacy-sewing.blend"


def grid(name, cuts, location):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cuts, y_subdivisions=cuts, size=2,
                                    location=location)
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.clothnext.add_physics()
    return obj


def side(cuts, which):
    n = cuts + 1
    return {"bottom": tuple(range(n)), "top": tuple(range(n * (n - 1), n * n)),
            "left": tuple(range(0, n * n, n)), "right": tuple(range(n - 1, n * n, n))}[which]


def export():
    entries = []
    for obj in bpy.context.scene.objects:
        if getattr(obj.cloth_next, "enabled", False):
            vertices, _edges, triangles = sewing._mesh(obj)
            entries.append(SimpleNamespace(obj=obj, role="CLOTH", boundary_vertices=vertices,
                                           boundary_triangles=triangles))
    _pairs, stitches = sewing.snapshot_enabled(bpy.context.scene, entries)
    rows = []
    for attachment, source_count, target_count in stitches:
        entry = wire_entry(attachment, source_vertex_count=source_count,
                           target_vertex_count=target_count)
        rows.append({"id": attachment.identifier, "ind": entry["ind"],
                     "w": [[round(v, 9) for v in row] for row in entry["w"]],
                     "stiffness": entry["stitch_stiffness"],
                     "same": entry["source_uuid"] == entry["target_uuid"]})
    return sorted(rows, key=lambda row: row["id"])


if mode == "write":
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    a, b = grid("Panel A", 8, (0, 0, 0)), grid("Panel B", 5, (3, 0, 0))
    scene = bpy.context.scene
    sewing._commit(scene, a, a, side(8, "bottom"), side(8, "top"))
    sewing._commit(scene, a, b, side(8, "left"), side(5, "right"))
    flipped = sewing._commit(scene, a, b, side(8, "right"), side(5, "left"))
    flipped.flipped = True
    flipped.strength = 35.0
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    (output / "legacy-export.json").write_text(json.dumps(export()), encoding="utf-8")
    print("LEGACY_WRITE_DONE", len(scene.cloth_next_sewing_definitions), flush=True)
else:
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    scene = bpy.context.scene
    expected = json.loads((output / "legacy-export.json").read_text(encoding="utf-8"))
    actual = export()
    records = sewing._seam_records(scene)
    report = {"definitions": len(scene.cloth_next_sewing_definitions),
              "all_valid": all(record.valid for record in records),
              "messages": [record.message for record in records],
              "export_identical": actual == expected,
              "stitch_rows": sum(len(row["ind"]) for row in actual)}
    if not report["export_identical"]:
        report["difference"] = [(e["id"], len(e["ind"]), a["id"], len(a["ind"]))
                                for e, a in zip(expected, actual) if e != a]
    print("LEGACY_READ", json.dumps(report), flush=True)
