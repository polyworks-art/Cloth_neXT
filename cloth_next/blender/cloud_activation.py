# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Activate a stored Cloud result on the open scene (main thread only).

The PC2 caches are written from the Import Snapshot and the archived raw
output (``cloud.importer``); the scene only decides *where* they attach and
*whether* that is safe (``cloud.activation``). Attaching reuses the local
Bake's ``_attach_playback``. The archive itself is never attached, so Clear
Result can only ever remove the activated copy.
"""

from __future__ import annotations

from pathlib import Path

import bpy

from .. import export_identity
from ..bake import pc2
from ..cloud import activation
from ..cloud.importer import import_result, load_archived_snapshot
from . import cloud_runtime


class ActivationRefused(RuntimeError):
    code = "import_incompatible"


def _objects_by_uuid(scene) -> dict:
    result = {}
    for obj in scene.objects:
        settings = getattr(obj, "cloth_next", None)
        if settings is None or not getattr(settings, "enabled", False):
            continue
        if not str(getattr(settings, "persistent_export_id", "") or ""):
            continue
        result[export_identity.export_uuid(obj)] = obj
    return result


def _local_result_time(obj, client_job_id: str) -> float | None:
    """Completion time of the attached result unless it is this job's own."""
    import json
    from ..bake import cache_metadata
    from .solver_test import simulation_modifiers
    for modifier in simulation_modifiers(obj):
        path = Path(bpy.path.abspath(getattr(modifier, "filepath", "") or ""))
        if not (path.name and path.is_file()):
            continue
        try:
            meta = json.loads(cache_metadata.sidecar_path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        if (meta.get("execution") or {}).get("client_job_id") == client_job_id:
            return None  # already this Cloud result: re-activation, not newer work
        return path.stat().st_mtime
    return None


def decide(context, client_job_id: str):
    """Explicit action: validate the scene once and classify the result."""
    from . import solver_test
    runtime = cloud_runtime.jobs()
    record = runtime.registry.get(client_job_id)
    snapshot = load_archived_snapshot(runtime.archive, client_job_id,
                                      record.snapshot_digest)
    objects = _objects_by_uuid(context.scene)
    validation = solver_test.validate_scene(context)
    topology = {export_identity.export_uuid(entry.obj): entry.topology_signature
                for entry in validation.deformables}
    current = {}
    for item in snapshot.objects:
        obj = objects.get(item["uuid"])
        if obj is None:
            continue
        current[item["uuid"]] = activation.CurrentObject(
            True, len(getattr(obj.data, "vertices", ())),
            topology.get(item["uuid"], ""), _local_result_time(obj, client_job_id))
    decision = activation.classify(
        snapshot.objects, current,
        settings_fingerprint=validation.settings_fingerprint,
        geometry_fingerprint=validation.geometry_fingerprint,
        submitted_at=record.created_at)
    return record, snapshot, objects, decision


def activate(context, client_job_id: str, *, confirmed: bool = False):
    """Write and attach the caches. Without ``confirmed`` only READY passes."""
    from . import solver_test
    record, snapshot, objects, decision = decide(context, client_job_id)
    if not decision.may_activate:
        raise ActivationRefused("; ".join(text for _u, verdict, text in decision.per_object
                                          if verdict is activation.Verdict.INCOMPATIBLE))
    if not decision.may_auto_activate and not confirmed:
        return decision
    pc2_paths = {}
    targets = []
    for item in snapshot.objects:
        obj = objects[item["uuid"]]
        root = Path(bpy.path.abspath(str(obj.cloth_next.cache_directory or "")))
        if not str(obj.cloth_next.cache_directory or "").strip():
            raise ActivationRefused(f"{obj.name}: set a Cache Directory first")
        path = root / f"cn_test_cloth_cloud_{client_job_id[:12]}_{item['uuid'][:8]}.pc2"
        pc2_paths[item["uuid"]] = path
        targets.append(solver_test.DeformablePlan(
            tuple(map(tuple, snapshot.initial_local(item["uuid"]).tolist())),
            tuple(tuple(row) for row in item["world_matrix"]), obj.name,
            item["uuid"], path, item["topology_signature"],
            dict(item["material_meta"]), item["role"],
            tuple(tuple(pair) for pair in item["stitch_pairs"]),
            float(item["stitch_snap_distance"])))
    runtime = cloud_runtime.jobs()
    headers = import_result(runtime.archive, client_job_id, snapshot=snapshot,
                            job_id=record.job_id, pc2_paths=pc2_paths)
    first = targets[0]
    frames = pc2.read_header(first.pc2_path).frame_count
    fingerprints = (first.material_meta or {}).get("fingerprints", {})
    plan = solver_test.RunPlan(
        None, None, first.initial_local, first.world_matrix, first.object_name,
        Path(bpy.app.tempdir or "."), first.pc2_path, frames,
        frame_start=snapshot.document["frames"]["start"],
        frame_end=snapshot.document["frames"]["start"] + frames - 1,
        fps=snapshot.document["frames"]["fps"],
        settings_fingerprint=fingerprints.get("settings", ""),
        geometry_fingerprint=fingerprints.get("geometry", ""),
        deformables=tuple(targets))
    solver_test._attach_playback(
        plan, headers if len(targets) > 1 else headers[first.uuid])
    return decision
