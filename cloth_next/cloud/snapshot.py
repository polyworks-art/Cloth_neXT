# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Import Snapshot: what turning raw solver output into PC2 needs (E22).

Everything the local import reads from ``RunPlan``/``DeformablePlan`` at
export time is frozen here, so a later import never falls back to the live
scene (an object moved since export would otherwise land shifted without
any error). The solver supplies the vertex map and, for SOLID objects, the
surface map; the snapshot supplies the rest.

Serialization is deterministic: one canonical JSON document plus one binary
blob of little-endian float32 initial positions (exactly the bytes PC2
sample 0 receives). The JSON names the blob's SHA-256, so the snapshot
digest (SHA-256 of the canonical JSON) covers both. Fields are copied by
name, never by dumping dataclasses, and the result is privacy-scanned.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Sequence

import numpy as np

from ..bake import result_import
from ..ppf.coordinates import ZUP_TO_YUP, solver_world_matrix
from . import privacy

SNAPSHOT_KIND = "cloth-next.cloud.import-snapshot"
SNAPSHOT_VERSION = 1
COORDINATE_CONVENTION = {
    "name": "blender-zup-to-ppf-yup",
    "version": 1,
    "zup_to_yup": [list(row) for row in ZUP_TO_YUP],
}
PC2_LAYOUT = {
    "start_frame": result_import.PC2_START_FRAME,
    "sample_rate": result_import.PC2_SAMPLE_RATE,
    "sample0": "exported initial state = Blender frame 1",
}
_FLOAT = np.dtype("<f4")
_OBJECT_TYPES = frozenset({"SHELL", "SOLID", "ROD"})


class SnapshotError(ValueError):
    pass


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True, allow_nan=False).encode("utf-8")


def _matrix(value) -> list[list[float]]:
    rows = [[float(component) for component in row] for row in value]
    if len(rows) != 4 or any(len(row) != 4 for row in rows):
        raise SnapshotError("matrix must be 4x4")
    if not np.isfinite(np.asarray(rows)).all():
        raise SnapshotError("matrix must be finite")
    return rows


def _sanitized_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the sidecar seed, dropping local install provenance.

    ``identities.solver.source_metadata`` describes how the local solver was
    installed (it can hold local paths) and plays no role in import or
    staleness. Everything else is kept and must pass the privacy scan.
    """
    if not meta:
        return {}
    result = copy.deepcopy(dict(meta))
    identities = result.get("identities")
    if isinstance(identities, dict) and isinstance(identities.get("solver"), dict):
        identities["solver"] = {key: value for key, value
                                in identities["solver"].items()
                                if key != "source_metadata"}
    return result


@dataclass(frozen=True, slots=True)
class ImportSnapshot:
    document: dict[str, Any]
    arrays: bytes

    @property
    def json_bytes(self) -> bytes:
        return canonical_bytes(self.document)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.json_bytes).hexdigest()

    @property
    def objects(self) -> tuple[dict[str, Any], ...]:
        return tuple(self.document["objects"])

    def initial_local(self, uuid: str) -> np.ndarray:
        for item in self.document["objects"]:
            if item["uuid"] == uuid:
                start = item["initial_local"]["offset"]
                count = item["vertex_count"]
                return np.frombuffer(self.arrays, dtype=_FLOAT,
                                     count=count * 3,
                                     offset=start).reshape((count, 3))
        raise SnapshotError(f"snapshot has no object {uuid}")

    def import_targets(self, pc2_paths: Mapping[str, Any]) \
            -> tuple[result_import.ImportTarget, ...]:
        """Targets for ``PlaybackCacheImport``; only ``pc2_paths`` is new."""
        from pathlib import Path
        targets = []
        for item in self.document["objects"]:
            targets.append(result_import.ImportTarget(
                item["uuid"], item["object_name"], Path(pc2_paths[item["uuid"]]),
                self.initial_local(item["uuid"]),
                tuple(tuple(row) for row in item["world_matrix"]),
                tuple(tuple(pair) for pair in item["stitch_pairs"]),
                float(item["stitch_snap_distance"]),
                copy.deepcopy(item["material_meta"])))
        return tuple(targets)

    def extraction_targets(self):
        from ..ppf.results import ExtractionTarget
        return tuple(ExtractionTarget(
            item["uuid"], item["vertex_count"], item["deformable_type"],
            tuple(tuple(row) for row in item["solver_world_matrix"]))
            for item in self.document["objects"])


def build_snapshot(*, deformables: Sequence[Any], session_objects: Sequence[Any],
                   frame_start: int, frame_end: int, frame_count: int,
                   fps: float, solver: Mapping[str, Any],
                   addon_version: str, payload_hashes: Mapping[str, str]) \
        -> ImportSnapshot:
    """Freeze ``DeformablePlan`` rows (+ their ``SessionDeformable``).

    ``deformables`` are ``DeformablePlan``-like objects, ``session_objects``
    the scene's ``dynamic_objects`` (same UUIDs) which carry the deformable
    type and the solver world matrix used for SOLID reconstruction.
    """
    sessions = {item.uuid: item for item in session_objects}
    if len(sessions) != len(session_objects):
        raise SnapshotError("duplicate solver object UUID")
    if frame_count < 2 or frame_end - frame_start + 1 != frame_count:
        raise SnapshotError("frame range and frame count disagree")
    objects = []
    blob = bytearray()
    for plan in deformables:
        session = sessions.get(plan.uuid)
        if session is None:
            raise SnapshotError(f"object {plan.uuid} is not a solver object")
        initial = np.ascontiguousarray(np.asarray(plan.initial_local),
                                       dtype=_FLOAT)
        if initial.ndim != 2 or initial.shape[1] != 3 or not len(initial):
            raise SnapshotError(f"object {plan.uuid} has no initial state")
        if initial.shape[0] != session.vertex_count:
            raise SnapshotError(f"object {plan.uuid} vertex count mismatch")
        if not np.isfinite(initial).all():
            raise SnapshotError(f"object {plan.uuid} has non-finite positions")
        deformable_type = str(session.deformable_type or "SHELL")
        if deformable_type not in _OBJECT_TYPES:
            raise SnapshotError(f"unsupported deformable type {deformable_type}")
        world = _matrix(plan.world_matrix)
        solver_world = _matrix(session.world_matrix
                               if session.world_matrix is not None
                               else solver_world_matrix(world))
        raw = initial.tobytes()
        pairs = [[int(a), int(b)] for a, b in plan.stitch_pairs]
        if any(not 0 <= index < initial.shape[0]
               for pair in pairs for index in pair):
            raise SnapshotError(f"object {plan.uuid} has an invalid seam pair")
        objects.append({
            "uuid": str(plan.uuid),
            "object_name": str(plan.object_name),
            "role": str(plan.role),
            "deformable_type": deformable_type,
            "vertex_count": int(initial.shape[0]),
            "topology_signature": str(plan.topology_signature),
            "world_matrix": world,
            "solver_world_matrix": solver_world,
            "stitch_pairs": pairs,
            "stitch_snap_distance": float(plan.stitch_snap_distance),
            "material_meta": _sanitized_meta(plan.material_meta),
            "initial_local": {"offset": len(blob), "bytes": len(raw),
                              "dtype": "<f4", "sha256":
                              hashlib.sha256(raw).hexdigest()},
        })
        blob.extend(raw)
    if not objects:
        raise SnapshotError("snapshot needs at least one object")
    arrays = bytes(blob)
    document = {
        "kind": SNAPSHOT_KIND,
        "version": SNAPSHOT_VERSION,
        "addon_version": str(addon_version),
        "coordinates": COORDINATE_CONVENTION,
        "pc2": PC2_LAYOUT,
        "frames": {"start": int(frame_start), "end": int(frame_end),
                   "count": int(frame_count), "fps": float(fps)},
        "solver": dict(solver),
        "payload_hashes": dict(sorted(payload_hashes.items())),
        "arrays": {"bytes": len(arrays),
                   "sha256": hashlib.sha256(arrays).hexdigest()},
        "objects": objects,
    }
    privacy.require_clean(document, label="Import Snapshot")
    # Round-trip once: guarantees allow_nan=False and canonical stability.
    return load_snapshot(canonical_bytes(document), arrays)


def load_snapshot(json_bytes: bytes, arrays: bytes, *,
                  expected_digest: str | None = None) -> ImportSnapshot:
    """Parse and fully verify a stored snapshot."""
    if expected_digest is not None and \
            hashlib.sha256(json_bytes).hexdigest() != expected_digest:
        raise SnapshotError("Import Snapshot digest mismatch")
    try:
        document = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SnapshotError("Import Snapshot is not valid JSON") from exc
    if not isinstance(document, dict) or document.get("kind") != SNAPSHOT_KIND:
        raise SnapshotError("not a Cloth NeXt Import Snapshot")
    if document.get("version") != SNAPSHOT_VERSION:
        raise SnapshotError("unsupported Import Snapshot version")
    if canonical_bytes(document) != json_bytes:
        raise SnapshotError("Import Snapshot is not canonical")
    if document.get("coordinates") != COORDINATE_CONVENTION:
        raise SnapshotError("unsupported coordinate convention")
    declared = document.get("arrays", {})
    if (declared.get("bytes") != len(arrays)
            or declared.get("sha256") != hashlib.sha256(arrays).hexdigest()):
        raise SnapshotError("Import Snapshot arrays do not match")
    for item in document.get("objects", ()):
        span = item["initial_local"]
        raw = arrays[span["offset"]:span["offset"] + span["bytes"]]
        if (span["bytes"] != item["vertex_count"] * 12
                or hashlib.sha256(raw).hexdigest() != span["sha256"]):
            raise SnapshotError(f"initial state of {item['uuid']} is corrupt")
    return ImportSnapshot(document, bytes(arrays))
