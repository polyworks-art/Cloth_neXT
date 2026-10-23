# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Downloaded raw output + Import Snapshot -> PC2 caches (no ``bpy``).

Uses exactly the local import path: ``ppf.results`` validates the vertex map
and reconstructs SOLID surfaces, ``bake.result_import`` applies the Sewing
snap, ``(Z2Y @ world)^-1`` from the snapshot and the streaming PC2 writer,
then writes the sidecar. The live scene is never read here (E22); callers
only choose where the caches go and whether attaching them is safe.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from ..bake import result_import
from ..ppf import results as solver_results
from .results import MAP_NAME, SURFACE_MAP_NAME, ResultArchive, ResultCorrupt
from .snapshot import ImportSnapshot, SnapshotError, load_snapshot


def load_archived_snapshot(archive: ResultArchive, client_job_id: str,
                           expected_digest: str) -> ImportSnapshot:
    return load_snapshot(archive.load_snapshot_bytes(client_job_id),
                         expected_digest=expected_digest)


def _with_actual_range(target: result_import.ImportTarget, frame_count: int,
                       partial: bool, snapshot: ImportSnapshot):
    """Sidecar seed for the frames actually delivered (E18 partial results)."""
    meta = copy.deepcopy(dict(target.material_meta))
    if not meta:
        return target
    meta.setdefault("expected", {})["frame_count"] = frame_count
    if partial:
        details = meta.setdefault("details", {})
        start = snapshot.document["frames"]["start"]
        details["partial_of_job"] = True
        details["blender_end_frame"] = start + frame_count - 1
    return replace(target, material_meta=meta)


def import_result(archive: ResultArchive, client_job_id: str, *,
                  snapshot: ImportSnapshot, job_id: str,
                  pc2_paths: Mapping[str, Path]) -> dict:
    """Write one PC2 (+ sidecar) per snapshot object; return PC2 headers.

    ``pc2_paths`` maps object UUID -> destination. The archive must already
    be verified complete for this job.
    """
    manifest = archive.load_manifest(client_job_id)
    if manifest.snapshot_digest != snapshot.digest:
        raise ResultCorrupt("result belongs to a different Import Snapshot")
    solver = snapshot.document["solver"]
    if manifest.build_id != solver["build_id"]:
        raise ResultCorrupt(
            f"result was computed by {manifest.build_id}, expected {solver['build_id']}")
    expected_frames = snapshot.document["frames"]["count"] - 1
    if not 1 <= manifest.frame_count <= expected_frames:
        raise ResultCorrupt("result has no usable frame range")
    partial = manifest.partial or manifest.frame_count < expected_frames
    pc2_frames = manifest.frame_count + 1

    schema_version = int(solver.get("schema_version") or 1) \
        if str(solver.get("schema_version", "")).isdigit() else 1
    extraction_targets = snapshot.extraction_targets()
    map_blob = archive.file_path(client_job_id, MAP_NAME).read_bytes()
    surface_path = archive.file_path(client_job_id, SURFACE_MAP_NAME)
    surface_blob = surface_path.read_bytes() if surface_path.is_file() else None
    _map, extraction = solver_results.prepare_output_extraction(
        map_blob, surface_blob, extraction_targets, schema_version=schema_version)

    targets = tuple(_with_actual_range(target, pc2_frames, partial, snapshot)
                    for target in snapshot.import_targets(pc2_paths))
    caches = result_import.PlaybackCacheImport(targets, frame_count=pc2_frames)
    try:
        caches.begin()
        caches.ensure_writers()
        for frame, raw in archive.iter_frames(client_job_id, manifest):
            positions = solver_results.decode_frame_payload_numpy(raw)
            caches.write_frame(solver_results.extract_dynamic_frame(
                positions, extraction, extraction_targets, frame=frame))
        execution = {"kind": "cloud", "job_id": job_id,
                     "client_job_id": client_job_id,
                     "build_id": manifest.build_id,
                     "snapshot_digest": snapshot.digest,
                     "solver_frames": manifest.frame_count,
                     "partial_of_job": partial}
        return caches.finalize(
            solver_identity={key: solver[key] for key in
                             ("package_version", "protocol_version",
                              "schema_version") if key in solver},
            execution=execution)
    except (SnapshotError, solver_results.ResultValidationError) as exc:
        caches.abort()
        raise ResultCorrupt(str(exc)) from exc
    except BaseException:
        caches.abort()
        raise
