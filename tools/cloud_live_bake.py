# SPDX-License-Identifier: GPL-3.0-or-later
"""A real Cloud Bake through the add-on against the PolyWorks Cloud.

    blender -b --factory-startup --python tools/cloud_live_bake.py -- \
        --repo <repo> --solver-root <copied GAIA root> --output <dir> \
        --blend <dunk.blend> --flip-cache <flip cache> --first 50 --last 60 \
        [--max-credits 1500] [--reference <local reference .pc2>]

Uses the artist's existing PolyWorks session (account vault of this OS user)
and the add-on's own Cloud path: Bake · Cloud packages the scene, the quote is
accepted only when it stays below ``--max-credits`` (this replaces the click
in Review & Send), then upload, submit, server state polling, automatic
download and activation. Water Flow is switched off (dry run). Every server
state change is written to ``report.json`` with a timestamp. Real credits of
the signed-in account are used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import addon_utils
import bpy


def parse():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--solver-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--flip-cache", type=Path, required=True)
    parser.add_argument("--first", type=int, default=50)
    parser.add_argument("--last", type=int, default=60)
    parser.add_argument("--max-credits", type=int, default=1500)
    parser.add_argument("--timeout-minutes", type=float, default=60.0)
    parser.add_argument("--settle-minutes", type=float, default=15.0)
    parser.add_argument("--reference", type=Path)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


ARGS = parse()
ARGS.output = ARGS.output.resolve()
ARGS.output.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(ARGS.repo.resolve()))
addon_utils.enable("cloth_next", default_set=True)
addon_utils.enable("bl_ext.superhivemarket_com.flip_fluids_addon", default_set=True)

from cloth_next.bake import pc2  # noqa: E402
from cloth_next.blender import cloud_runtime, cloud_ui, solver_test  # noqa: E402
from cloth_next.cloud import package as cloud_package, service  # noqa: E402
from cloth_next.cloud.registry import LocalState  # noqa: E402
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver  # noqa: E402
from cloth_next.updater import engines  # noqa: E402

REPORT = {"blender": bpy.app.version_string, "checks": {}, "timeline": []}
STARTED = time.time()


def save():
    (ARGS.output / "report.json").write_text(
        json.dumps(REPORT, indent=2, default=str), encoding="utf-8")


def check(name, ok, detail=""):
    REPORT["checks"][name] = {"ok": bool(ok), "detail": detail}
    save()
    print(("PASS " if ok else "FAIL ") + name, json.dumps(detail, default=str)[:600],
          flush=True)
    return ok


def job_of(client_job_id):
    return next((job for job in cloud_runtime.view().jobs
                 if job.client_job_id == client_job_id), None)


_last_seen = {}


def note(record):
    """Append a timeline row whenever a reported value changes."""
    if record is None:
        return
    row = {"state": record.state.value, "server_state": record.server_state,
           "frames": record.progress_frames, "total": record.total_frames,
           "queue": record.queue_position, "gpu": record.gpu_type_id,
           "reserved": record.billing_reserved, "finalized": record.billing_finalized,
           "final_cost": record.final_cost, "elapsed_ms": record.elapsed_ms,
           "error": record.error_code}
    if row != _last_seen.get(record.client_job_id):
        _last_seen[record.client_job_id] = dict(row)
        row["t"] = round(time.time() - STARTED, 1)
        REPORT["timeline"].append(row)
        save()
        print("STATE", json.dumps(row), flush=True)


def wait(predicate, seconds, client_job_id="", refresh_every=10.0, direct=False):
    """Pump the add-on's main-thread timer; ask the server every few seconds.

    While the job runs the runtime's own "refresh" action is queued (worker
    thread). ``direct`` re-reads one settled job here, when the worker is idle.
    """
    deadline = time.monotonic() + seconds
    next_refresh = time.monotonic() + refresh_every
    while time.monotonic() < deadline:
        cloud_runtime.tick()
        if client_job_id:
            note(job_of(client_job_id))
        if predicate():
            return True
        if client_job_id and time.monotonic() >= next_refresh:
            next_refresh = time.monotonic() + refresh_every
            record = job_of(client_job_id)
            if record is not None and record.submitted_confirmed:
                if direct:
                    cloud_runtime.jobs().refresh(client_job_id)
                    cloud_runtime._publish(jobs=cloud_runtime.jobs().registry.all())
                else:
                    cloud_runtime.enqueue("refresh")
        time.sleep(0.1)
    return False


# Solver: the copied GAIA build, identified like an installed one ------------
ROOT = ARGS.solver_root.resolve()
RESOLVED = SolverResolver(solver_test._version_probe).resolve(SolverResolutionContext(
    development_executable=ROOT / "target/cuda/release/ppf-cts-server.exe"))
BUILD = engines.read_gaia_build(ROOT)
SOLVER = {"engine": "GAIA", "build_id": BUILD.build_id, "version": BUILD.version,
          "package_version": RESOLVED.package_version,
          "protocol_version": RESOLVED.protocol_version,
          "schema_version": RESOLVED.schema_version,
          **cloud_package.native_hashes(RESOLVED)}
solver_test.resolve_solver = lambda _context: RESOLVED
cloud_package.solver_identity = lambda _resolved: dict(SOLVER)
REPORT["solver"] = SOLVER

# Real PolyWorks Cloud, real session --------------------------------------------
service.install_backend(service.default_factory())
client = service.backend()
capabilities = client.capabilities()
service.record_capabilities(capabilities)
account = client.account()
mapping = capabilities.mapping_for({"engine": "GAIA", "build_id": BUILD.build_id,
                                    "solver_sha256": SOLVER["solver_sha256"],
                                    "worker_sha256": SOLVER["worker_sha256"],
                                    "protocol": "0.26", "payload_schema": "2"})
check("server_unlocked", service.availability().available and service.unlocked(),
      {"enabled": capabilities.enabled, "execution": capabilities.execution_enabled,
       "billing": capabilities.billing_mode})
check("account_may_submit", account.get("cloud_enabled") and account.get("can_submit"),
      account)
check("desktop_build_mapped", mapping is not None,
      mapping and {"server_build": mapping.server_build})
cloud_runtime.reset()

# Scene: light dunk test, dry -------------------------------------------------
bpy.ops.wm.open_mainfile(filepath=str(ARGS.blend.resolve()))
prefs = bpy.context.preferences.addons["cloth_next"].preferences
prefs.auto_launch_bake_window = False
prefs.bake_location = "CLOUD"
prefs.cloud_auto_download = True
objects = [o for o in bpy.context.scene.objects
           if o.cloth_next.enabled and o.cloth_next.role == "CLOTH"]
if not objects and "Sleeve" in bpy.data.objects:
    for name, role in (("Sleeve", "CLOTH"), ("Arm", "COLLIDER")):
        obj = bpy.data.objects[name]
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.clothnext.add_physics()
        if role != "CLOTH":
            bpy.ops.clothnext.set_object_type(role=role)
    sleeve = bpy.data.objects["Sleeve"]
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
if domains:
    domains[0].flip_fluid.domain.cache.cache_directory = str(ARGS.flip_cache)
for obj in objects:
    s = obj.cloth_next
    s.bake_start, s.bake_end = ARGS.first, ARGS.last
    s.cache_directory = str(ARGS.output / "cache")
    s.water_flow_enabled = False
    s.water_hydrodynamic_pressure = False
bpy.context.view_layer.objects.active = objects[0]
bpy.context.scene.cloth_next_recovery.enabled = False
bpy.context.scene.frame_set(ARGS.first)
cloth = objects[0]

# 1. Bake · Cloud ----------------------------------------------------------------
result = bpy.ops.clothnext.bake("EXEC_DEFAULT")
while solver_test.shared_controller.snapshot().active:
    solver_test._startup_pump()
    solver_test._pump()
    time.sleep(0.02)
snapshot = solver_test.shared_controller.snapshot()
if not check("package_built", snapshot.state.value == "FINISHED"
             and "Package ready" in snapshot.status_message,
             {"result": list(result), "state": snapshot.state.value,
              "message": snapshot.status_message, "error": snapshot.error_details}):
    sys.exit(1)
record = cloud_runtime.view().jobs[-1]
job_id = record.client_job_id
REPORT["client_job_id"] = job_id

# 2. Quote → Review & Send -------------------------------------------------------
quoted = wait(lambda: job_of(job_id).state is LocalState.AWAITING_APPROVAL
              or bool(cloud_runtime.view().error_code), 120.0, job_id)
record = job_of(job_id)
quote = {"maximum": record.quote_maximum, "unit": record.quote_unit,
         "gpu": record.quote_gpu, "usage_percent": record.quote_usage_percent,
         "max_seconds": record.quote_max_seconds, "expires": record.quote_expires_at,
         "billing_mode": record.quote_billing_mode}
REPORT["quote"] = quote
if not check("quote_received", quoted and record.state is LocalState.AWAITING_APPROVAL,
             {**quote, "message": cloud_runtime.view().message}):
    sys.exit(1)
if not check("quote_within_limit", (record.quote_maximum or 0) <= ARGS.max_credits,
             {"maximum": record.quote_maximum, "limit": ARGS.max_credits}):
    cloud_runtime.enqueue("cancel", job_id)
    wait(lambda: False, 10.0, job_id)
    sys.exit(1)
cloud_runtime.approve(job_id, record.quote_id)          # the Review & Send click

# 3. Upload + submit -------------------------------------------------------------
# accept_quote parks the job as UPLOAD_PAUSED until the worker takes it, so
# only a confirmed submit, a failure or a Cloud error ends this wait.
submitted = wait(lambda: job_of(job_id).submitted_confirmed
                 or job_of(job_id).state is LocalState.FAILED
                 or bool(cloud_runtime.view().error_code), 900.0, job_id)
record = job_of(job_id)
REPORT["server_job_id"] = record.job_id
if not check("submitted", submitted and record.submitted_confirmed,
             {"state": record.state.value, "job_id": record.job_id,
              "message": cloud_runtime.view().message}):
    sys.exit(1)

# 4. Server run: queued → starting → running → succeeded --------------------------
seen = set()
def finished():
    job = job_of(job_id)
    if job.server_state:
        seen.add(job.server_state)
    return job.state in (LocalState.STORED, LocalState.FAILED, LocalState.CANCELLED,
                         LocalState.STOPPED_AT_LIMIT, LocalState.EXPIRED)
done = wait(finished, ARGS.timeout_minutes * 60.0, job_id)
record = job_of(job_id)
check("pod_started_automatically", bool({"starting", "running"} & seen)
      or record.state is LocalState.STORED,
      {"server_states_seen": sorted(seen), "gpu": record.gpu_type_id})
if not check("result_stored", done and record.state is LocalState.STORED,
             {"state": record.state.value, "error": record.error_code,
              "message": cloud_runtime.view().message}):
    sys.exit(1)

# 5. Activation (automatic) and PC2 check -----------------------------------------
attached = wait(lambda: any(m.type == "MESH_CACHE" and "cloud" in m.filepath
                            for m in cloth.modifiers), 60.0)
cache = next((m.filepath for m in cloth.modifiers if m.type == "MESH_CACHE"), "")
check("auto_activated", attached, {"modifier_cache": cache})
if cache:
    import numpy as np
    path = Path(bpy.path.abspath(cache))
    header = pc2.read_header(path)
    frames = np.asarray([frame for frame in pc2.iter_frames(path)])
    detail = {"samples": len(frames), "expected": ARGS.last - ARGS.first + 1,
              "vertices": header.vertex_count}
    if ARGS.reference and ARGS.reference.is_file():
        reference = np.asarray([frame for frame in pc2.iter_frames(ARGS.reference)])
        a, b = frames, reference
        if a.shape == b.shape:
            detail["max_diff_m_vs_local_reference"] = float(np.abs(a - b).max())
            detail["frame0_identical"] = bool((a[0] == b[0]).all())
    check("pc2_imported", len(frames) == ARGS.last - ARGS.first + 1, detail)

# 6. Final runtime + credits ------------------------------------------------------
settled = wait(lambda: job_of(job_id).billing_finalized, ARGS.settle_minutes * 60.0,
               job_id, refresh_every=15.0, direct=True)
record = job_of(job_id)
check("billing_finalized", settled and record.final_cost is not None,
      {"final_cost": record.final_cost, "elapsed_ms": record.elapsed_ms,
       "gpu": record.gpu_type_id, "quote_maximum": record.quote_maximum})
REPORT["job_record"] = record.to_json()
save()
print("LIVE CLOUD BAKE DONE", flush=True)
