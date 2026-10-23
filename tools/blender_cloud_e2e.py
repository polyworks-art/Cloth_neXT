# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloud Bake through the real UI path in Blender, against the test backend.

    blender -b --factory-startup --python tools/blender_cloud_e2e.py -- \
        --repo <repo> --solver-root <copied GAIA root> --output <dir> [--frames 12]

Bake Location = Cloud, ``bpy.ops.clothnext.bake`` packages the scene, the
Cloud worker uploads and submits, the test backend runs the real solver, the
add-on downloads and activates automatically. Then: moved object ->
STALE_SETTINGS, changed topology -> INCOMPATIBLE, Clear Result keeps the
archive. The backend is the in-process test double, not the PolyWorks Cloud.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import addon_utils
import bpy
import numpy as np


def parse():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--solver-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=12)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


ARGS = parse()
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=False)
os.environ["CLOTH_NEXT_ACCOUNT_ROOT"] = str(ARGS.output / "account")
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)

from cloth_next.bake import pc2  # noqa: E402
from cloth_next.blender import cloud_activation, cloud_runtime, cloud_ui, solver_test  # noqa: E402
from cloth_next.cloud import package as cloud_package, service  # noqa: E402
from cloth_next.cloud.activation import Verdict  # noqa: E402
from cloth_next.cloud.registry import LocalState  # noqa: E402
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver  # noqa: E402
from cloth_next.updater import engines  # noqa: E402
from tests.cloud_backend import CloudTestBackend, solver_executor  # noqa: E402

REPORT = {"blender": bpy.app.version_string, "checks": {}}


def save():
    (ARGS.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str),
                                             encoding="utf-8")


def check(name, ok, detail=""):
    REPORT["checks"][name] = {"ok": bool(ok), "detail": str(detail)[:400]}
    save()
    print(("PASS " if ok else "FAIL ") + name, detail, flush=True)


ROOT = ARGS.solver_root.resolve()
RESOLVED = SolverResolver(solver_test._version_probe).resolve(SolverResolutionContext(
    development_executable=ROOT / "target/cuda/release/ppf-cts-server.exe"))
BUILD = engines.read_gaia_build(ROOT)
SOLVER = {"engine": "GAIA", "build_id": BUILD.build_id, "version": BUILD.version,
          "package_version": RESOLVED.package_version, "protocol_version":
          RESOLVED.protocol_version, "schema_version": RESOLVED.schema_version}
solver_test.resolve_solver = lambda _context: RESOLVED
# The copied solver has no registry installation; identify it like Blender would.
cloud_package.solver_identity = lambda _resolved: dict(SOLVER)
backend = CloudTestBackend(builds=(BUILD.build_id,), executor=solver_executor(
    RESOLVED, ARGS.output / "server", BUILD.build_id))
service.install_backend(lambda: backend)
service.record_capabilities(backend.capabilities())
cloud_runtime.reset()

prefs = bpy.context.preferences.addons["cloth_next"].preferences
prefs.auto_launch_bake_window = False
prefs.bake_location = "CLOUD"
prefs.cloud_auto_download = True

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_grid_add(x_subdivisions=20, y_subdivisions=20, size=1.0,
                                location=(0.0, 0.0, 1.2))
sheet = bpy.context.object
sheet.name = "CloudSheet"
bpy.ops.clothnext.add_physics()
sheet.cloth_next.bake_start, sheet.cloth_next.bake_end = 1, ARGS.frames
sheet.cloth_next.cache_directory = str(ARGS.output / "cache")
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.35, location=(0.0, 0.0, 0.6))
bpy.ops.clothnext.add_physics()
bpy.ops.clothnext.set_object_type(role="COLLIDER")
bpy.context.view_layer.objects.active = sheet

# 1. Bake · Cloud: package, controller finishes, Blender is free again.
started = time.monotonic()
result = bpy.ops.clothnext.bake("EXEC_DEFAULT")
while solver_test.shared_controller.snapshot().active:
    solver_test._startup_pump()
    solver_test._pump()
    time.sleep(0.02)
snapshot = solver_test.shared_controller.snapshot()
check("bake_cloud_packages_and_finishes",
      snapshot.state.value == "FINISHED" and "Package ready" in snapshot.status_message,
      f"{result} {snapshot.state.value} {snapshot.status_message} {snapshot.error_details}")
check("no_local_cache_written", not list((ARGS.output / "cache").glob("*.pc2")))


def wait(predicate, seconds=60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        cloud_runtime.tick()
        if predicate():
            return True
        time.sleep(0.05)
    return False


queued = wait(lambda: any(job.state is LocalState.QUEUED for job in cloud_runtime.view().jobs))
check("upload_and_submit_confirmed", queued, cloud_runtime.view().message)
backend.run_pending()                       # real GAIA run on this PC
cloud_runtime.enqueue("refresh")
stored = wait(lambda: any(job.state is LocalState.STORED
                          for job in cloud_runtime.view().jobs), 120.0)
job = cloud_runtime.view().jobs[-1]
check("auto_download", stored, f"{job.state.value} {cloud_runtime.view().message}")
attached = wait(lambda: any(m.type == "MESH_CACHE" and "cloud" in m.filepath
                            for m in sheet.modifiers), 30.0)
check("auto_activate_unchanged_scene", attached,
      [(m.type, getattr(m, "filepath", "")) for m in sheet.modifiers])

check("job_listed_for_this_file",
      [j.client_job_id for j in cloud_ui.file_jobs(bpy.context.scene, cloud_runtime.view().jobs)]
      == [job.client_job_id], bpy.context.scene.cloth_next_cloud_job_ids)

# 2. Playback equals the activated PC2.
modifier = next(m for m in sheet.modifiers if m.type == "MESH_CACHE")
path = Path(bpy.path.abspath(modifier.filepath))
frames = list(pc2.iter_frames(path))
error = 0.0
for index, expected in enumerate(frames, 1):
    bpy.context.scene.frame_set(index)
    evaluated = sheet.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        positions = np.array([v.co[:] for v in mesh.vertices])
        error = max(error, float(np.abs(positions - np.asarray(expected)).max()))
    finally:
        evaluated.to_mesh_clear()
check("playback_matches_pc2", len(frames) == ARGS.frames and error < 1e-5,
      f"frames={len(frames)} error={error}")
inspection = solver_test.inspect_attached_cache(sheet)
check("sidecar_ready", getattr(inspection, "condition", None) is not None
      and inspection.condition.value == "READY", getattr(inspection, "message", ""))

# 3. Moved object -> STALE_SETTINGS (stored, not attached automatically).
sheet.location.x += 0.5
_record, _snapshot, _objects, decision = cloud_activation.decide(
    bpy.context, job.client_job_id)
check("moved_object_is_stale_settings", decision.verdict is Verdict.STALE_SETTINGS,
      decision.per_object)
sheet.location.x -= 0.5

# 4. Changed topology -> INCOMPATIBLE, never attached.
bpy.ops.object.select_all(action="DESELECT")
sheet.select_set(True)
bpy.context.view_layer.objects.active = sheet
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.mesh.subdivide()
bpy.ops.object.mode_set(mode="OBJECT")
try:
    cloud_activation.activate(bpy.context, job.client_job_id, confirmed=True)
    refused = False
except cloud_activation.ActivationRefused as exc:
    refused = True
    detail = str(exc)
check("changed_topology_is_refused", refused, detail if refused else "attached")

# 5. Clear Result never deletes the archive.
bpy.ops.clothnext.solver_test_clear()
check("clear_result_keeps_archive",
      cloud_runtime.jobs().archive.is_complete(job.client_job_id))

cloud_runtime.shutdown()
REPORT["result"] = "PASS" if all(item["ok"] for item in REPORT["checks"].values()) else "FAIL"
save()
print(json.dumps({"result": REPORT["result"]}), flush=True)
