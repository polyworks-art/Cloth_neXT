# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloud Bakes Phase 0: one real end-to-end pass on this machine.

    blender -b --factory-startup --python tools/cloud_phase0.py -- \
        --repo <repo> --solver-root <copied GAIA/AETHER root> --engine GAIA \
        --scene cloth|solid|sewing|water --output <dir> [--frames 24] \
        [--blend <NEON copy> --first 130 --last 140]

Steps (concept section 3):
1. A normal Bake through ``bpy.ops.clothnext.bake`` produces the local
   reference; its ``RunPlan`` is captured as it is handed to the worker.
2. ``cloud.package.build_package`` packages that same plan (immutable store,
   manifest, Import Snapshot).
3. ``CloudJobs`` uploads it to the in-process test backend (resumable,
   deduplicated, idempotent) and submits.
4. The backend's executor runs the real solver (``SolverSession``) on the
   package files alone and archives the raw output it produced.
5. The raw output is downloaded block by block with hash checks.
6. The scene is emptied; the result is imported from the snapshot only.
7. Cloud and local PC2 are compared.

The "server" is this PC and the same solver build, so step 7 measures the
run-to-run spread of the solver, not GPU or driver differences.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
    parser.add_argument("--engine", choices=("GAIA", "AETHER"), required=True)
    parser.add_argument("--scene", choices=("cloth", "contact", "solid", "sewing", "water"),
                        required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--reproducible", action="store_true",
                        help="GAIA fast deterministic path (no static colliders)")
    parser.add_argument("--blend", type=Path)
    parser.add_argument("--flip-cache", type=Path)
    parser.add_argument("--character-dna", action="store_true")
    parser.add_argument("--dry", action="store_true",
                        help="water scene with Water Flow off (control run)")
    parser.add_argument("--first", type=int, default=130)
    parser.add_argument("--last", type=int, default=140)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


ARGS = parse()
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(ARGS.repo.resolve()))
if ARGS.scene == "water":
    repos = bpy.context.preferences.extensions.repos
    for module in ("superhivemarket_com", "api_portal_polyhammer_com"):
        if not any(r.module == module for r in repos):
            repos.new(name=module, module=module)
addon_utils.enable("cloth_next", default_set=True)

from cloth_next import manifest_version  # noqa: E402
from cloth_next.bake import pc2  # noqa: E402
from cloth_next.blender import solver_test  # noqa: E402
from cloth_next.cloud.importer import import_result, load_archived_snapshot  # noqa: E402
from cloth_next.cloud.jobs import CloudJobs  # noqa: E402
from cloth_next.cloud.package import (HYDRO_MODEL_ENV, HYDRO_VOLUME_ENV,  # noqa: E402
                                      build_package)
from cloth_next.cloud.package_store import PackageStore  # noqa: E402
from cloth_next.cloud.registry import JobRegistry  # noqa: E402
from cloth_next.cloud.results import ResultArchive  # noqa: E402
from cloth_next.ppf import results as solver_results  # noqa: E402
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver  # noqa: E402
from cloth_next.ppf_run.session import (SessionDeformable, SessionScene,  # noqa: E402
                                        SolverSession, new_project_name)
from cloth_next.updater import engines  # noqa: E402
from tests.cloud_backend import CloudTestBackend, solver_executor  # noqa: E402

def _addon_commit() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(ARGS.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


REPORT = {"addon_commit": _addon_commit(), "scene": ARGS.scene, "engine": ARGS.engine, "reproducible": ARGS.reproducible, "blender": bpy.app.version_string,
          "addon_version": manifest_version(), "steps": {}}


def save():
    (ARGS.output / "report.json").write_text(json.dumps(REPORT, indent=2, default=str),
                                             encoding="utf-8")


def server_executable(root: Path) -> Path:
    for relative in ("target/cuda/release/ppf-cts-server.exe",
                     "target/cpu/release/ppf-cts-server.exe"):
        if (root / relative).is_file():
            return root / relative
    raise FileNotFoundError(f"no ppf-cts-server.exe below {root}")


def engine_identity(root: Path, resolved) -> dict:
    common = {"package_version": resolved.package_version or "unknown",
              "protocol_version": resolved.protocol_version or "unknown",
              "schema_version": resolved.schema_version or "unknown"}
    from cloth_next.cloud.package import native_hashes
    common.update(native_hashes(resolved))
    build = engines.read_gaia_build(root)
    if build is not None:
        return {"engine": "GAIA", "build_id": build.build_id, "version": build.version,
                "source_revision": build.source_revision, "platform": build.platform,
                "backend": build.backend, **common}
    tag = root.name if root.name != "aether" else "2026-10-13-12-32"
    return {"engine": "AETHER", "build_id": tag, "version": tag, "release_tag": tag,
            **common}


ROOT = ARGS.solver_root.resolve()
RESOLVED = SolverResolver(solver_test._version_probe).resolve(
    SolverResolutionContext(development_executable=server_executable(ROOT)))
if ARGS.reproducible:
    from dataclasses import replace as _replace
    RESOLVED = _replace(RESOLVED, solver_environment=engines.REPRODUCIBLE_ENVIRONMENT)
SOLVER = engine_identity(ROOT, RESOLVED)
assert SOLVER["engine"] == ARGS.engine, SOLVER
solver_test.resolve_solver = lambda _context: RESOLVED
REPORT["solver"] = SOLVER


# Scenes ---------------------------------------------------------------------

def clear():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def deformable(name, primitive, *, role="CLOTH", **kwargs):
    primitive(**kwargs)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.clothnext.add_physics()
    if role != "CLOTH":
        bpy.ops.clothnext.set_object_type(role=role)
    obj.cloth_next.bake_start, obj.cloth_next.bake_end = 1, ARGS.frames
    obj.cloth_next.cache_directory = str(ARGS.output / "local")
    return obj


def collider_sphere(location=(0.0, 0.0, 0.6)):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.35, location=location)
    sphere = bpy.context.object
    sphere.name = "DrapeSphere"
    bpy.ops.clothnext.add_physics()
    bpy.ops.clothnext.set_object_type(role="COLLIDER")


def scene_cloth():
    clear()
    deformable("DrapeSheet", bpy.ops.mesh.primitive_grid_add, x_subdivisions=24,
               y_subdivisions=24, size=1.0, location=(0.0, 0.0, 1.2))
    collider_sphere()


def scene_contact():
    clear()
    deformable("ContactSheetLower", bpy.ops.mesh.primitive_grid_add, x_subdivisions=24,
               y_subdivisions=24, size=1.0, location=(0.0, 0.0, 1.0))
    lower = bpy.context.object
    group = lower.vertex_groups.new(name="Pins")
    coords = [(v.co.x, v.co.y) for v in lower.data.vertices]
    corners = sorted({min(range(len(coords)), key=lambda i: sx * coords[i][0] + sy * coords[i][1])
                      for sx in (-1, 1) for sy in (-1, 1)})
    group.add(corners, 1.0, "REPLACE")
    lower.cloth_next.pinning_enabled = True
    lower.cloth_next.pin_group = group.name
    lower.cloth_next.pin_mode = "STATIC"
    deformable("ContactSheetUpper", bpy.ops.mesh.primitive_grid_add, x_subdivisions=24,
               y_subdivisions=24, size=1.0, location=(0.0, 0.0, 1.25))


def scene_solid():
    clear()
    deformable("SoftCube", bpy.ops.mesh.primitive_cube_add, role="SOFT_BODY",
               size=0.4, location=(0.0, 0.0, 1.3))
    deformable("DrapeSheet", bpy.ops.mesh.primitive_grid_add, x_subdivisions=16,
               y_subdivisions=16, size=1.0, location=(0.0, 0.0, 1.8))
    collider_sphere()


def scene_sewing():
    from cloth_next.blender import sewing
    clear()
    left = deformable("Left", bpy.ops.mesh.primitive_grid_add, x_subdivisions=10,
                      y_subdivisions=10, size=1.0, location=(-0.7, 0, 1.2))
    right = deformable("Right", bpy.ops.mesh.primitive_grid_add, x_subdivisions=14,
                       y_subdivisions=14, size=1.0, location=(0.7, 0, 1.2))
    tube = deformable("Tube", bpy.ops.mesh.primitive_grid_add, x_subdivisions=12,
                      y_subdivisions=12, size=1.0, location=(0, 2.0, 1.2))

    def side(cuts, which):
        n = cuts + 1
        return {"left": tuple(range(0, n * n, n)),
                "right": tuple(range(n - 1, n * n, n))}[which]
    scene = bpy.context.scene
    cross = sewing._commit(scene, left, right, side(10, "right"), side(14, "left"))
    intra = sewing._commit(scene, tube, tube, side(12, "left"), side(12, "right"))
    cross.strength = intra.strength = 100.0


def scene_water():
    addon_utils.enable("bl_ext.superhivemarket_com.flip_fluids_addon", default_set=True)
    if ARGS.character_dna:  # the NEON scene rigs its character with Character DNA
        for module in ("bl_ext.api_portal_polyhammer_com.character_dna",
                       "bl_ext.api_portal_polyhammer_com.character_assembly"):
            addon_utils.enable(module, default_set=True)
    bpy.ops.wm.open_mainfile(filepath=str(ARGS.blend.resolve()))
    if ARGS.character_dna:
        from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller, engine
        controller.register()
        controller.after_load()
        for obj in bpy.context.scene.objects:
            if obj.animation_data:
                for curve in obj.animation_data.drivers:
                    if engine.NAMESPACE in curve.driver.expression:
                        curve.driver.expression = curve.driver.expression
            obj.update_tag()
    prefs = bpy.context.preferences.addons["cloth_next"].preferences
    prefs.auto_launch_bake_window = False
    objects = [o for o in bpy.context.scene.objects
               if o.cloth_next.enabled and o.cloth_next.role == "CLOTH"]
    if not objects and "Sleeve" in bpy.data.objects:
        # Light dunk-test scene: a sleeve on an animated arm in a small pool.
        for name, role in (("Sleeve", "CLOTH"), ("Arm", "COLLIDER")):
            obj = bpy.data.objects[name]
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.clothnext.add_physics()
            if role != "CLOTH":
                bpy.ops.clothnext.set_object_type(role=role)
        sleeve = bpy.data.objects["Sleeve"]
        # Hold the top rim in place so the sleeve stays inside the prepared
        # water volume instead of dropping out of it.
        heights = [(sleeve.matrix_world @ v.co).z for v in sleeve.data.vertices]
        top = max(heights) - 0.05 * (max(heights) - min(heights))
        group = sleeve.vertex_groups.new(name="Rim")
        group.add([i for i, z in enumerate(heights) if z >= top], 1.0, "REPLACE")
        sleeve.cloth_next.pinning_enabled = True
        sleeve.cloth_next.pin_group = group.name
        sleeve.cloth_next.pin_mode = "STATIC"
        objects = [sleeve]
    domains = [o for o in bpy.context.scene.objects
               if getattr(getattr(o, "flip_fluid", None), "object_type", "") == "TYPE_DOMAIN"]
    domains[0].flip_fluid.domain.cache.cache_directory = str(ARGS.flip_cache)
    for obj in objects:
        s = obj.cloth_next
        s.bake_start, s.bake_end = ARGS.first, ARGS.last
        s.cache_directory = str(ARGS.output / "local")
        s.water_flow_enabled = not ARGS.dry
        s.water_hydrodynamic_pressure = not ARGS.dry
        s.water_flow_domain = domains[0]
        s.water_flow_container = str(ARGS.output / "Fluid.gaia")
        s.water_flow_influence = 1.0
        s.water_flow_velocity_scale = 1.0
    bpy.context.view_layer.objects.active = objects[0]
    bpy.context.scene.cloth_next_recovery.enabled = False
    bpy.context.scene.frame_set(ARGS.first)


{"cloth": scene_cloth, "contact": scene_contact, "solid": scene_solid, "sewing": scene_sewing,
 "water": scene_water}[ARGS.scene]()
bpy.context.preferences.addons["cloth_next"].preferences.auto_launch_bake_window = False


# Step 1: local Bake, capturing the exact plan -----------------------------------

CAPTURED = {}
original_start = solver_test._start_prepared_run


def capture(plan):
    CAPTURED["plan"] = plan
    return original_start(plan)


solver_test._start_prepared_run = capture
started = time.monotonic()
assert bpy.ops.clothnext.bake("EXEC_DEFAULT") & {"FINISHED", "RUNNING_MODAL"}
from cloth_next.blender import water_preparation  # noqa: E402
while solver_test.shared_controller.snapshot().active:
    if time.monotonic() - started > 7200:
        solver_test.request_cancel()
        raise TimeoutError("local Bake exceeded two hours")
    if solver_test._pin_capture is not None:
        solver_test._pin_capture_pump()
    if water_preparation.active():
        water_preparation._pump()
    solver_test._startup_pump()
    solver_test._pump()
    time.sleep(0.02)
snapshot = solver_test.shared_controller.snapshot()
REPORT["steps"]["1_local_bake"] = {
    "state": snapshot.state.value, "error": snapshot.error_summary,
    "seconds": round(time.monotonic() - started, 2)}
save()
assert snapshot.state.value == "FINISHED", snapshot.error_details
plan = CAPTURED["plan"]
targets = solver_test._plan_deformables(plan)
local_paths = {target.uuid: Path(target.pc2_path) for target in targets}
REPORT["objects"] = [{"name": t.object_name, "uuid": t.uuid,
                      "vertices": len(t.initial_local)} for t in targets]
REPORT["steps"]["1_local_bake"]["export_timings"] = dict(plan.export_timings)


# Step 2: package -------------------------------------------------------------------

account = ARGS.output / "account"
store = PackageStore(account / "cloud-packages")
step = time.monotonic()
package = build_package(
    plan, targets, store=store, work_directory=ARGS.output / "package-work",
    addon_version=manifest_version(), blender_version=bpy.app.version_string,
    job_name=f"phase0-{ARGS.scene}", solver=SOLVER)
REPORT["steps"]["2_package"] = {
    "seconds": round(time.monotonic() - step, 2), "timings": package.timings,
    "files": [{"role": f.role, "name": f.name, "bytes": f.size} for f in package.files
              if f.role != "hydro-volume"],
    "hydro_volume_files": sum(1 for f in package.files if f.role == "hydro-volume"),
    "hydro_volume_bytes": sum(f.size for f in package.files if f.role == "hydro-volume"),
    "total_bytes": package.total_bytes, "snapshot_digest": package.snapshot.digest,
    "payload_hashes_match_local": (
        next(item["sha256"] for item in package.manifest["files"]
             if item["role"] == "data") == plan.scene.data_hash)}
(ARGS.output / "manifest.json").write_text(json.dumps(package.manifest, indent=2),
                                           encoding="utf-8")
save()


# Step 3-5: transfer, real execution, download -----------------------------------------

EXECUTE_REPORT = {}
real_executor = solver_executor(RESOLVED, ARGS.output / "server",
                                engine_identity(ROOT, RESOLVED)["build_id"],
                                report=EXECUTE_REPORT)


backend = CloudTestBackend(executor=real_executor, builds=[{
    "engine": SOLVER["engine"], "build_id": SOLVER["build_id"],
    "solver_sha256": SOLVER["solver_sha256"], "worker_sha256": SOLVER["worker_sha256"],
    "server_build": SOLVER["build_id"]}])
registry = JobRegistry(account)
archive = ResultArchive(account / "cloud-results")
jobs = CloudJobs(backend, registry, store, archive, upload_block=4 * 1024 * 1024)
jobs.register(package)
quoted = jobs.request_quote(package.client_job_id)
jobs.accept_quote(package.client_job_id, quoted.quote_id)
step = time.monotonic()
record = jobs.upload_and_submit(package.client_job_id)
REPORT["steps"]["3_transfer"] = {"seconds": round(time.monotonic() - step, 2),
                                 "state": record.state.value,
                                 "uploaded_bytes": record.upload_bytes_done}
save()
backend.run_pending()
record = jobs.refresh(package.client_job_id)
REPORT["steps"]["4_execute"] = dict(EXECUTE_REPORT, state=record.state.value)
step = time.monotonic()
record = jobs.download(package.client_job_id)
manifest = archive.load_manifest(package.client_job_id)
REPORT["steps"]["5_download"] = {
    "seconds": round(time.monotonic() - step, 2), "state": record.state.value,
    "build_id": manifest.build_id,
    "bytes": sum(item.size for item in manifest.files),
    "files": len(manifest.files), "frames": manifest.frame_count,
    "surface_map": any(item.name == "surface-map.bin" for item in manifest.files)}
save()


# Step 6: import from the snapshot only --------------------------------------------------

bpy.ops.wm.read_factory_settings(use_empty=True)  # the original scene is gone
step = time.monotonic()
snapshot = load_archived_snapshot(archive, package.client_job_id, record.snapshot_digest)
cloud_paths = {uuid: ARGS.output / "cloud" / path.name for uuid, path in local_paths.items()}
import_result(archive, package.client_job_id, snapshot=snapshot, job_id=record.job_id,
              pc2_paths=cloud_paths)
REPORT["steps"]["6_import"] = {"seconds": round(time.monotonic() - step, 2),
                               "scene_objects_at_import": len(bpy.data.objects)}
save()


# Step 7: compare --------------------------------------------------------------------

comparison = {}
for uuid, local in local_paths.items():
    cloud = cloud_paths[uuid]
    local_header, cloud_header = pc2.read_header(local), pc2.read_header(cloud)
    a = np.asarray(list(pc2.iter_frames(local)), dtype=np.float64)
    b = np.asarray(list(pc2.iter_frames(cloud)), dtype=np.float64)
    extent = float(np.linalg.norm(a[0].max(axis=0) - a[0].min(axis=0))) or 1.0
    difference = np.linalg.norm(a - b, axis=2)
    comparison[uuid] = {
        "header_identical": local_header == cloud_header,
        "frames": int(a.shape[0]), "vertices": int(a.shape[1]),
        "frame0_identical": bool(np.array_equal(a[0], b[0])),
        "bit_identical": local.read_bytes() == cloud.read_bytes(),
        "max_m": float(difference.max()), "mean_m": float(difference.mean()),
        "max_relative_to_extent": float(difference.max() / extent),
        "per_frame_max_m": [round(float(v), 6) for v in difference.max(axis=1)],
        "local_sha256": hashlib.sha256(local.read_bytes()).hexdigest(),
        "cloud_sha256": hashlib.sha256(cloud.read_bytes()).hexdigest()}
REPORT["steps"]["7_compare"] = comparison
REPORT["result"] = "PASS" if all(item["header_identical"] and item["frame0_identical"]
                                 for item in comparison.values()) else "FAIL"
save()
print(json.dumps({"result": REPORT["result"]}), flush=True)
