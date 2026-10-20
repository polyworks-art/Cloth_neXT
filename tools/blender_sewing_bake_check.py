# SPDX-License-Identifier: GPL-3.0-or-later
"""Real bake with authored seams: sewn sides must pull together dynamically.

    blender -b --factory-startup --python tools/blender_sewing_bake_check.py -- \
        <repo> <ppf-cts-server.exe> <out-dir> [frames]

Builds two separate panels sewn across a gap plus one panel closed into a
tube by a same-object seam, bakes them with the given solver executable and
reports how far apart the sewn vertex pairs are on the first and last frame.
"""
import json
import sys
import time
from pathlib import Path

import addon_utils
import bpy
import numpy as np

repo, solver, out, *rest = sys.argv[sys.argv.index("--") + 1:]
frames = int(rest[0]) if rest else 24
out = Path(out)
out.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, repo)
addon_utils.enable("cloth_next", default_set=True)
from cloth_next.blender import sewing, solver_test  # noqa: E402
from cloth_next.bake.pc2 import iter_frames  # noqa: E402
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver  # noqa: E402

resolved = SolverResolver(solver_test._version_probe).resolve(
    SolverResolutionContext(development_executable=Path(solver)))
solver_test.resolve_solver = lambda _context: resolved
bpy.context.preferences.addons["cloth_next"].preferences.auto_launch_bake_window = False

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)


def grid(name, cuts, size, location):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=cuts, y_subdivisions=cuts, size=size,
                                    location=location)
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.clothnext.add_physics()
    obj.cloth_next.bake_start, obj.cloth_next.bake_end = 1, frames
    obj.cloth_next.cache_directory = str(out)
    return obj


def side(cuts, which):
    n = cuts + 1
    return {"bottom": tuple(range(n)), "top": tuple(range(n * (n - 1), n * n)),
            "left": tuple(range(0, n * n, n)), "right": tuple(range(n - 1, n * n, n))}[which]


left = grid("Left", 10, 1.0, (-.7, 0, 1.2))
right = grid("Right", 14, 1.0, (.7, 0, 1.2))       # different density
tube = grid("Tube", 12, 1.0, (0, 2.0, 1.2))
scene = bpy.context.scene
cross = sewing._commit(scene, left, right, side(10, "right"), side(14, "left"))
intra = sewing._commit(scene, tube, tube, side(12, "left"), side(12, "right"))
cross.strength = intra.strength = 100.0


def world_frames(obj):
    modifiers = [m for m in obj.modifiers
                 if solver_test.is_cloth_next_playback_modifier(obj, m) and m.filepath]
    if not modifiers:
        return None
    matrix = np.array(obj.matrix_world)
    result = []
    for frame in iter_frames(Path(bpy.path.abspath(modifiers[0].filepath))):
        local = np.asarray(frame, dtype=np.float64).reshape(-1, 3)
        result.append(local @ matrix[:3, :3].T + matrix[:3, 3])
    return result


start = time.monotonic()
bpy.ops.clothnext.bake("EXEC_DEFAULT")
while solver_test.shared_controller.snapshot().active:
    if time.monotonic() - start > 1800:
        solver_test.request_cancel()
        break
    if solver_test._pin_capture is not None:
        solver_test._pin_capture_pump()
    solver_test._startup_pump()
    solver_test._pump()
    time.sleep(.02)
snapshot = solver_test.shared_controller.snapshot()
report = {"state": snapshot.state.value, "error": snapshot.error_summary,
          "seconds": round(time.monotonic() - start, 1), "seams": {}}
for item in scene.cloth_next_sewing_definitions:
    source = bpy.data.objects[item.source_name]
    target = bpy.data.objects[item.target_name]
    source_frames, target_frames = world_frames(source), world_frames(target)
    if source_frames is None or target_frames is None:
        report["seams"][item.name] = "no cache"
        continue
    pairs = np.array([(row.source_index, row.target_index) for row in item.mapping])

    def gap(index):
        a = source_frames[index][pairs[:, 0]]
        b = target_frames[index][pairs[:, 1]]
        return float(np.sqrt(((a - b) ** 2).sum(axis=1)).max())
    report["seams"][item.name] = {"first_gap_m": round(gap(0), 4),
                                  "last_gap_m": round(gap(-1), 4),
                                  "frames": len(source_frames)}
(out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
print("SEWING_BAKE", json.dumps(report), flush=True)
bpy.ops.wm.quit_blender()
