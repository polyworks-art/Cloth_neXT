# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an immutable Cloud package from an exported Bake (no ``bpy``).

Input is the ``RunPlan`` the local Bake builds; nothing is re-exported. The
same Water Flow preparation a local run performs (``prepare_solver_inputs``)
turns the scene into plain solver input files first, so the server only has
to run the solver:

``data``                Scene payload (``upload_atomic`` data)
``param``               Param payload (native fluid already encoded in)
``official-bindings``   Sewing / face friction rows for the official frontend
``hydro-model``         Hydrodynamic Pressure model (when used)
``hydro/<name>``        Hydrodynamic Pressure volumes (when used)
``import-snapshot``     Import Snapshot file (opaque to the server)

Every file is copied into the ``PackageStore`` before the manifest exists,
hashes are over the uncompressed bytes, and the manifest is privacy-scanned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import time
import uuid
from typing import Any, Callable, Mapping

from . import privacy
from .package_store import PackageStore, StoredBlob
from .snapshot import ImportSnapshot, build_snapshot, canonical_bytes

PACKAGE_SCHEMA = 1
MANIFEST_KIND = "cloth-next.cloud.package"
HYDRO_MODEL_ENV = "PPF_EXPERIMENTAL_HYDRO_TEST_JSON"
HYDRO_VOLUME_ENV = "PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR"
# Local preparation logs; they name local FLIP paths and are not solver input.
_HYDRO_SKIP = frozenset({"preparation-progress.json", "preparation-report.json"})
_HYDRO_PROVENANCE_KEEP = ("source_fingerprint", "first_source_frame",
                          "last_source_frame", "source_fps", "held_final_frame",
                          "method", "limits")


class PackageError(ValueError):
    """``code`` maps to a CNX Cloud error code."""

    def __init__(self, message: str, code: str = "invalid_package") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PackageFile:
    role: str
    name: str
    sha256: str
    size: int

    def record(self) -> dict[str, Any]:
        return {"role": self.role, "name": self.name, "sha256": self.sha256,
                "size": self.size,
                "transport": {"compression": "none", "size": self.size}}


@dataclass(frozen=True, slots=True)
class CloudPackage:
    client_job_id: str
    manifest: dict[str, Any]
    manifest_sha256: str
    files: tuple[PackageFile, ...]
    snapshot: ImportSnapshot
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def hashes(self) -> tuple[str, ...]:
        return tuple(sorted({item.sha256 for item in self.files}
                            | {self.manifest_sha256}))

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.files)


def solver_identity(resolved) -> dict[str, Any]:
    """Exact engine build a Cloud run must use (E13); never a fallback.

    GAIA is identified by its ``build_id``; AETHER by its verified official
    release tag. Unverified external solvers cannot run in the Cloud.
    """
    from ..updater import engines
    installation = getattr(resolved, "installation", None)
    build = engines.gaia_build_of(installation)
    common = {
        "package_version": resolved.package_version or "unknown",
        "protocol_version": resolved.protocol_version or "unknown",
        "schema_version": resolved.schema_version or "unknown",
    }
    if build is not None:
        return {"engine": "GAIA", "build_id": build.build_id,
                "version": build.version, "source_revision": build.source_revision,
                "platform": build.platform, "backend": build.backend, **common}
    if installation is not None and engines.verified_upstream(installation):
        tag = str(installation.official_release_tag)
        return {"engine": "AETHER", "build_id": f"aether:{tag}",
                "release_tag": tag, **common}
    raise PackageError(
        "Cloud Bakes need GAIA or a verified official AETHER release; the "
        "selected solver is an unverified external build.",
        code="build_unverifiable")


def _payload_source(payload) -> tuple[Path | None, bytes | None]:
    if isinstance(payload, (str, Path)):
        return Path(payload), None
    return None, bytes(payload)


def _store(store: PackageStore, payload, *, expected_sha256: str | None = None) \
        -> StoredBlob:
    path, raw = _payload_source(payload)
    if path is not None:
        return store.put_file(path, expected_sha256=expected_sha256)
    return store.put_bytes(raw, expected_sha256=expected_sha256)


def _payload_size(payload) -> int:
    path, raw = _payload_source(payload)
    return path.stat().st_size if path is not None else len(raw)


def _sanitized_hydro_manifest(path: Path) -> bytes:
    document = json.loads(path.read_text(encoding="utf-8"))
    provenance = document.get("provenance") or {}
    document["provenance"] = {key: provenance[key]
                              for key in _HYDRO_PROVENANCE_KEEP
                              if key in provenance}
    return json.dumps(document, indent=2, sort_keys=True).encode("utf-8")


def _hydro_files(environment: Mapping[str, str]) \
        -> tuple[Path | None, list[tuple[str, Path]]]:
    if not environment:
        return None, []
    model = Path(environment[HYDRO_MODEL_ENV])
    volume = Path(environment[HYDRO_VOLUME_ENV])
    files = []
    for path in sorted(volume.rglob("*")):
        if path.is_file() and path.name not in _HYDRO_SKIP:
            files.append((path.relative_to(volume).as_posix(), path))
    return model, files


def build_package(plan, deformables, *, store: PackageStore,
                  work_directory: Path, addon_version: str,
                  blender_version: str, job_name: str,
                  prepare_inputs: Callable | None = None,
                  check_cancel: Callable[[], None] = lambda: None,
                  event: Callable[..., None] = lambda *a, **k: None,
                  client_job_id: str | None = None,
                  solver: Mapping[str, Any] | None = None,
                  reserve_bytes: int = 256 * 1024 * 1024) -> CloudPackage:
    """Package one exported multi-object ``RunPlan`` for a Cloud run.

    ``solver`` overrides the engine identity only for test doubles; Blender
    always derives it from the resolved installation.
    """
    timings: dict[str, float] = {}
    started = time.monotonic()
    solver = dict(solver) if solver is not None else solver_identity(plan.resolved)
    if prepare_inputs is None:
        from ..ppf_run.session import prepare_solver_inputs as prepare_inputs
    step = time.monotonic()
    Path(work_directory).mkdir(parents=True, exist_ok=True)
    prepared = prepare_inputs(plan.scene, plan.resolved, Path(work_directory),
                              check_cancel=check_cancel, event=event)
    timings["water_preparation"] = time.monotonic() - step
    scene = prepared.scene
    if scene.water_stream_json:
        raise PackageError(
            "This Water Flow mode streams from a local helper and cannot run "
            "in the Cloud. Use GAIA 0.26 Water Flow.", code="water_mode_unsupported")
    model_path, hydro = _hydro_files(prepared.environment)
    estimate = (_payload_size(scene.data_payload) + _payload_size(scene.param_payload)
                + sum(path.stat().st_size for _name, path in hydro))
    store.require_space(estimate, reserve_bytes=reserve_bytes)

    step = time.monotonic()
    files: list[PackageFile] = []
    data = _store(store, scene.data_payload, expected_sha256=scene.data_hash)
    files.append(PackageFile("data", "data", data.sha256, data.size))
    param = _store(store, scene.param_payload, expected_sha256=scene.param_hash)
    files.append(PackageFile("param", "param", param.sha256, param.size))
    if scene.official_bridge_json:
        bindings = store.put_bytes(scene.official_bridge_json.encode("utf-8"))
        files.append(PackageFile("official-bindings", "official-bindings.json",
                                 bindings.sha256, bindings.size))
    if model_path is not None:
        model = store.put_file(model_path)
        files.append(PackageFile("hydro-model", "hydro-model.json",
                                 model.sha256, model.size))
        for name, path in hydro:
            check_cancel()
            blob = (store.put_bytes(_sanitized_hydro_manifest(path))
                    if name == "hydrodynamics.json" else store.put_file(path))
            files.append(PackageFile("hydro-volume", f"hydro/{name}",
                                     blob.sha256, blob.size))
    timings["store_inputs"] = time.monotonic() - step

    step = time.monotonic()
    snapshot = build_snapshot(
        deformables=deformables, session_objects=scene.dynamic_objects,
        frame_start=plan.frame_start, frame_end=plan.frame_end,
        frame_count=plan.frame_count, fps=plan.fps, solver=solver,
        addon_version=addon_version,
        payload_hashes={"data": scene.data_hash, "param": scene.param_hash})
    snapshot_file = store.put_bytes(snapshot.file_bytes,
                                    expected_sha256=snapshot.digest)
    files.append(PackageFile("import-snapshot", "import-snapshot",
                             snapshot_file.sha256, snapshot_file.size))
    timings["snapshot"] = time.monotonic() - step

    client_job_id = client_job_id or uuid.uuid4().hex
    objects = [{
        "uuid": item["uuid"], "deformable_type": item["deformable_type"],
        "vertex_count": item["vertex_count"],
        "solver_world_matrix": item["solver_world_matrix"],
    } for item in snapshot.objects]
    manifest = {
        "kind": MANIFEST_KIND,
        "package_schema": PACKAGE_SCHEMA,
        "client_job_id": client_job_id,
        "display_name": str(job_name)[:120],
        "addon_version": str(addon_version),
        "blender_version": str(blender_version),
        "solver": solver,
        "frames": snapshot.document["frames"],
        "quality": {"deterministic": bool(getattr(plan.resolved,
                                                  "solver_environment", ()))},
        "water": ("hydrodynamic" if model_path is not None else
                  "native-fluid" if prepared.native_fluid_seconds is not None
                  else "none"),
        "objects": objects,
        "fingerprints": {
            "settings": plan.settings_fingerprint,
            "geometry": plan.geometry_fingerprint,
        },
        "payload_hashes": {"data": scene.data_hash, "param": scene.param_hash},
        "snapshot_digest": snapshot.digest,
        "files": [item.record() for item in files],
    }
    privacy.require_clean(manifest, label="Cloud package manifest")
    manifest_blob = store.put_bytes(canonical_bytes(manifest))
    store.add_reference(client_job_id,
                        [item.sha256 for item in files] + [manifest_blob.sha256])
    timings["total"] = time.monotonic() - started
    return CloudPackage(client_job_id, manifest, manifest_blob.sha256,
                        tuple(files), snapshot, timings)


def manifest_file(manifest: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    for item in manifest["files"]:
        if item["role"] == role:
            return item
    raise PackageError(f"package has no {role} file")
