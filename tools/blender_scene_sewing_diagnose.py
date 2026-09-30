# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspect and simulate a saved Sewing scene without modifying the blend file."""
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cloth_next
from cloth_next.blender import solver_test
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.schema import envelope
from cloth_next.ppf_run.session import SolverSession


def properties(value, depth=0):
    result = {}
    for prop in value.bl_rna.properties:
        key = prop.identifier
        if key == "rna_type":
            continue
        item = getattr(value, key)
        if prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            result[key] = list(item) if getattr(prop, "is_array", False) else item
        elif prop.type == "POINTER" and depth < 3 and isinstance(item, bpy.types.PropertyGroup):
            result[key] = properties(item, depth + 1)
    return result


args = sys.argv[sys.argv.index("--") + 1:]
blend, executable, output = map(Path, args[:3])
frame_end = int(args[3]) if len(args) > 3 else 40
output.mkdir(parents=True, exist_ok=True)
cloth_next.register()
bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False)
report = {
    "scene": str(blend), "frame": bpy.context.scene.frame_current,
    "objects": [{"name": obj.name, "settings": properties(obj.cloth_next),
                 "vertices": len(obj.data.vertices),
                 "modifiers": [(mod.name, mod.type) for mod in obj.modifiers]}
                for obj in bpy.context.scene.objects if obj.type == "MESH"],
    "seams": [{**properties(item),
               "mapping": [(row.source_index, row.target_index) for row in item.mapping]}
              for item in bpy.context.scene.cloth_next_sewing_definitions],
}
resolved = SolverResolver(solver_test._version_probe).resolve(
    SolverResolutionContext(development_executable=executable))
solver_test.resolve_solver = lambda context: resolved
for obj in bpy.context.scene.objects:
    if obj.type == "MESH":
        obj.cloth_next.cache_directory = str(output / "cache")
        if obj.cloth_next.enabled:
            obj.cloth_next.bake_end = frame_end
            bpy.context.view_layer.objects.active = obj
try:
    plan = solver_test.build_run_plan(bpy.context)
    report["params"] = envelope.loads_envelope(
        plan.scene.param_payload, envelope.KIND_PARAM, schema_version=2)
    frames = []
    session = SolverSession(resolved=resolved, scene=plan.scene,
                            work_directory=output / "solver", frame_sink=frames.append)
    try:
        session.run()
        report["outcome"] = "SUCCESS"
    except Exception as exc:
        report["outcome"] = "FAILED"
        report["error"] = str(exc)
        report["violations"] = list(getattr(exc, "violations", ()))
        if hasattr(exc, "record"):
            report["presentation"] = solver_test._present_worker_error(plan, exc)
    report["completed_frames"] = len(frames)
    if frames:
        import numpy as np
        final = frames[-1].positions_by_uuid
        report["final_seam_distances"] = [
            {"source_uuid": seam["source_uuid"], "target_uuid": seam["target_uuid"],
             "max_distance": float(max(
                 np.linalg.norm(
                     sum(weight * final[seam["source_uuid"]][index]
                         for index, weight in zip(indices[:3], weights[:3]))
                     - sum(weight * final[seam["target_uuid"]][index]
                           for index, weight in zip(indices[3:], weights[3:])))
                 for indices, weights in zip(seam["ind"], seam["w"])))}
            for seam in report["params"].get("cross_stitch", ())]
    report["diagnostics"] = repr(session.diagnostics)
except Exception as exc:
    report["outcome"] = "EXPORT_FAILED"
    report["error"] = str(exc)
(output / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
print("SCENE_DIAGNOSIS", report["outcome"], report.get("completed_frames"), report.get("error", ""), flush=True)
