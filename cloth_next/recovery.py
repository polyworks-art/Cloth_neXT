# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional metadata for solver-owned recovery projects.

The solver owns scene input, output frames and ``state_<N>.bin.gz`` files.
This module only publishes a project as resumable after those files and the
server response have both been verified.  It deliberately contains no bpy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import hashlib
import gzip
import json
import os
from pathlib import Path
import tempfile
import time

RECOVERY_SCHEMA_VERSION = 2
# Schema 3 used the identical project/checkpoint wire layout, but recorded the
# internal format number in every identity.  Keep it readable so a rollback to
# this release cannot orphan checkpoints created by that short-lived build.
_READABLE_RECOVERY_SCHEMA_VERSIONS = frozenset({2, 3})
METADATA_NAME = "metadata.json"


class ProjectState(str, Enum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    CHECKPOINT_REQUESTED = "CHECKPOINT_REQUESTED"
    CHECKPOINT_CONFIRMED = "CHECKPOINT_CONFIRMED"
    SAVED = "SAVED"
    RESUMABLE = "RESUMABLE"
    RESUMING = "RESUMING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"
    DELETED = "DELETED"


_TRANSITIONS = {
    ProjectState.NEW: {ProjectState.RUNNING, ProjectState.RESUMING,
                       ProjectState.ABANDONED, ProjectState.DELETED},
    ProjectState.RUNNING: {
        ProjectState.CHECKPOINT_REQUESTED, ProjectState.CHECKPOINT_CONFIRMED,
        ProjectState.SAVED, ProjectState.RESUMABLE, ProjectState.FINISHED,
        ProjectState.FAILED, ProjectState.ABANDONED},
    ProjectState.CHECKPOINT_REQUESTED: {
        ProjectState.CHECKPOINT_CONFIRMED, ProjectState.SAVED,
        ProjectState.RESUMABLE, ProjectState.FAILED, ProjectState.ABANDONED},
    ProjectState.CHECKPOINT_CONFIRMED: {
        ProjectState.RUNNING, ProjectState.CHECKPOINT_REQUESTED,
        ProjectState.SAVED, ProjectState.RESUMABLE, ProjectState.FINISHED,
        ProjectState.FAILED, ProjectState.ABANDONED},
    ProjectState.SAVED: {ProjectState.RESUMABLE, ProjectState.FAILED,
                         ProjectState.DELETED},
    ProjectState.RESUMABLE: {ProjectState.RESUMING, ProjectState.ABANDONED,
                             ProjectState.DELETED},
    ProjectState.RESUMING: {
        ProjectState.RUNNING, ProjectState.CHECKPOINT_REQUESTED,
        ProjectState.CHECKPOINT_CONFIRMED, ProjectState.SAVED,
        ProjectState.RESUMABLE, ProjectState.FINISHED, ProjectState.FAILED,
        ProjectState.ABANDONED},
    ProjectState.FINISHED: {ProjectState.DELETED},
    ProjectState.FAILED: {ProjectState.RESUMABLE, ProjectState.RESUMING,
                          ProjectState.ABANDONED, ProjectState.DELETED},
    ProjectState.ABANDONED: {ProjectState.DELETED},
    ProjectState.DELETED: set(),
}


@dataclass(frozen=True, slots=True)
class RecoveryIdentity:
    scene_key: str
    param_key: str
    export_uuids: tuple[str, ...]
    geometry_fingerprint: str
    topology_fingerprint: str
    frame_start: int
    frame_end: int
    fps: float
    collider_sampling: tuple[tuple[str, int], ...]
    solver_version: str
    protocol_version: str
    solver_schema_version: str
    recovery_schema_version: int = RECOVERY_SCHEMA_VERSION
    solver_installation_id: str = "legacy-unregistered"
    solver_release_tag: str | None = None


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    frame: int
    project_id: str
    identity: RecoveryIdentity
    created_at: float
    checkpoint_path: str
    checkpoint_size: int
    checkpoint_sha256: str
    integrity: str = "VERIFIED"


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    project_id: str
    state: ProjectState
    identity: RecoveryIdentity
    server_data_root: str
    project_root: str
    checkpoints: tuple[CheckpointRecord, ...] = ()
    partial_pc2: tuple[tuple[str, str], ...] = ()
    last_frame: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str = ""
    generation: int = 0


@dataclass(frozen=True, slots=True)
class Compatibility:
    compatible: bool
    reason: str
    params_changed: bool = False


# States in which a solver project may still own a resumable Saved State.
# A hard abort (host kill, keyboard interrupt, watchdog) leaves the metadata
# in RUNNING or RESUMING; cooperative saves land in CHECKPOINT_*/SAVED/
# RESUMABLE; a failed run is only resumable when a verified checkpoint
# survived.
_RESUMABLE_STATES = frozenset({
    ProjectState.RUNNING,
    ProjectState.CHECKPOINT_REQUESTED,
    ProjectState.CHECKPOINT_CONFIRMED,
    ProjectState.SAVED,
    ProjectState.RESUMABLE,
    ProjectState.RESUMING,
    ProjectState.FAILED,
})


@dataclass(frozen=True, slots=True)
class ResumeEligibility:
    """One authoritative answer to "can this checkpoint be resumed?".

    ``available`` reflects only the durable on-disk state; ``compatible``
    additionally requires a matching identity.  When no identity is supplied
    (Blender file load) compatibility is unknown and ``resumable`` falls back
    to ``available``; the caller must re-verify with a real identity before
    acting on it.
    """

    available: bool
    resumable: bool
    compatible: bool | None
    state: ProjectState | None
    project_id: str | None
    latest_checkpoint_frame: int
    checkpoint_count: int
    generation: int
    error: str
    reason: str


def recovery_root(cache_directory: Path, scene_key: str) -> Path:
    return Path(cache_directory) / ".cloth_next_recovery" / scene_key


def metadata_path(cache_directory: Path, scene_key: str) -> Path:
    return recovery_root(cache_directory, scene_key) / METADATA_NAME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        # Windows can briefly deny replacement while a virus scanner or
        # indexer has the existing file open without delete sharing. Keep the
        # flushed temporary file private and retry only the atomic publication
        # step for a short, bounded period.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def cleanup_temporary_files(root: Path) -> tuple[Path, ...]:
    removed = []
    try:
        candidates = tuple(Path(root).glob(".*.tmp"))
    except OSError:
        return ()
    for path in candidates:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return tuple(removed)


def _identity_dict(identity: RecoveryIdentity) -> dict:
    value = asdict(identity)
    value["export_uuids"] = list(identity.export_uuids)
    value["collider_sampling"] = [
        list(item) for item in identity.collider_sampling]
    return value


def _identity_from_dict(value: dict, *, stored_schema_version: int | None = None) \
        -> RecoveryIdentity:
    data = dict(value)
    data["export_uuids"] = tuple(str(item) for item in data["export_uuids"])
    data["collider_sampling"] = tuple(
        (str(name), int(samples))
        for name, samples in data["collider_sampling"])
    if stored_schema_version == 3:
        # Schema 3 has the same durable representation as Schema 2.  Its
        # differing embedded marker must not make an otherwise verified
        # checkpoint incompatible merely because the reader was rolled back.
        data["recovery_schema_version"] = RECOVERY_SCHEMA_VERSION
    return RecoveryIdentity(**data)


def _record_dict(record: CheckpointRecord) -> dict:
    value = asdict(record)
    value["identity"] = _identity_dict(record.identity)
    return value


def _record_from_dict(value: dict, *, stored_schema_version: int | None = None) \
        -> CheckpointRecord:
    return CheckpointRecord(
        frame=int(value["frame"]), project_id=str(value["project_id"]),
        identity=_identity_from_dict(
            value["identity"], stored_schema_version=stored_schema_version),
        created_at=float(value["created_at"]),
        checkpoint_path=str(value["checkpoint_path"]),
        checkpoint_size=int(value["checkpoint_size"]),
        checkpoint_sha256=str(value["checkpoint_sha256"]),
        integrity=str(value.get("integrity", "VERIFIED")))


def _verified_checkpoint(record: CheckpointRecord) -> bool:
    path = Path(record.checkpoint_path)
    try:
        if (record.integrity != "VERIFIED" or not path.is_file()
                or record.checkpoint_size <= 0
                or path.stat().st_size != record.checkpoint_size
                or _sha256(path) != record.checkpoint_sha256):
            return False
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as stream:
                return bool(stream.read(1))
        return True
    except (OSError, EOFError):
        return False


def _project_dict(record: ProjectRecord) -> dict:
    return {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "project": {
            "project_id": record.project_id,
            "state": record.state.value,
            "identity": _identity_dict(record.identity),
            "server_data_root": record.server_data_root,
            "project_root": record.project_root,
            "checkpoints": [_record_dict(item)
                            for item in record.checkpoints],
            "partial_pc2": [list(item) for item in record.partial_pc2],
            "last_frame": record.last_frame,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "error": record.error,
            "generation": record.generation,
        },
    }


def publish_project(path: Path, record: ProjectRecord) -> ProjectRecord:
    _atomic_json(Path(path), _project_dict(record))
    return record


def create_project(path: Path, *, project_id: str,
                   identity: RecoveryIdentity, server_data_root: Path,
                   project_root: Path,
                   partial_pc2: tuple[tuple[str, str], ...] = ()) \
        -> ProjectRecord:
    record = ProjectRecord(
        project_id=str(project_id), state=ProjectState.NEW,
        identity=identity, server_data_root=str(Path(server_data_root).resolve()),
        project_root=str(Path(project_root).resolve()),
        partial_pc2=tuple(sorted(partial_pc2)))
    return publish_project(path, record)


def load_project(path: Path, *, verify_checkpoints: bool = True) \
        -> ProjectRecord | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        stored_schema_version = int(raw["schema_version"])
        if stored_schema_version not in _READABLE_RECOVERY_SCHEMA_VERSIONS:
            return None
        value = raw["project"]
        checkpoints = tuple(
            _record_from_dict(item, stored_schema_version=stored_schema_version)
            for item in value["checkpoints"])
        if verify_checkpoints:
            checkpoints = tuple(item for item in checkpoints
                                if _verified_checkpoint(item))
        record = ProjectRecord(
            project_id=str(value["project_id"]),
            state=ProjectState(str(value["state"])),
            identity=_identity_from_dict(
                value["identity"], stored_schema_version=stored_schema_version),
            server_data_root=str(value["server_data_root"]),
            project_root=str(value["project_root"]),
            checkpoints=checkpoints,
            partial_pc2=tuple(
                (str(uuid), str(path))
                for uuid, path in value.get("partial_pc2", ())),
            last_frame=int(value.get("last_frame", 0)),
            created_at=float(value["created_at"]),
            updated_at=float(value["updated_at"]),
            error=str(value.get("error", "")),
            generation=int(value.get("generation", 0)))
        if not Path(record.project_root).is_dir():
            return replace(record, state=ProjectState.ABANDONED,
                           error="Recovery project missing")
        return record
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def transition(path: Path, record: ProjectRecord, state: ProjectState, *,
               last_frame: int | None = None,
               checkpoints: tuple[CheckpointRecord, ...] | None = None,
               partial_pc2: tuple[tuple[str, str], ...] | None = None,
               error: str = "") -> ProjectRecord:
    state = ProjectState(state)
    if state != record.state and state not in _TRANSITIONS[record.state]:
        raise ValueError(
            f"invalid recovery transition {record.state.value} -> "
            f"{state.value}")
    updated = replace(
        record, state=state, updated_at=time.time(),
        last_frame=(record.last_frame if last_frame is None
                    else int(last_frame)),
        checkpoints=(record.checkpoints if checkpoints is None
                     else checkpoints),
        partial_pc2=(record.partial_pc2 if partial_pc2 is None
                     else tuple(sorted(partial_pc2))),
        error=str(error), generation=record.generation + 1)
    return publish_project(path, updated)


def checkpoint_path(project_root: Path, frame: int) -> Path:
    return (Path(project_root) / "session" / "output"
            / f"state_{int(frame)}.bin.gz")


def owned_server_data_root(metadata_path: Path) -> Path:
    """The only solver-data root a Recovery record may authorize deleting."""
    return (Path(metadata_path).resolve().parent / "server-data").resolve()


def owned_project_root(metadata_path: Path, record: ProjectRecord) -> Path | None:
    """Return a proven Cloth NeXt project root, never a metadata-trusted path."""
    expected_server = owned_server_data_root(metadata_path)
    server = Path(record.server_data_root).resolve()
    project = Path(record.project_root).resolve()
    if server != expected_server or project == server:
        return None
    if server not in project.parents:
        return None
    return project


def owned_checkpoint_path(metadata_path: Path, record: ProjectRecord,
                          item: CheckpointRecord) -> Path | None:
    project = owned_project_root(metadata_path, record)
    if project is None:
        return None
    candidate = Path(item.checkpoint_path).resolve()
    expected = checkpoint_path(project, item.frame).resolve()
    return candidate if candidate == expected else None


def owned_partial_path(metadata_path: Path, uuid: str, value: str) -> Path | None:
    partial_root = (Path(metadata_path).resolve().parent / "partials").resolve()
    candidate = Path(value).resolve()
    expected = (partial_root / f"{uuid}.pc2.partial").resolve()
    return candidate if candidate == expected else None


def confirm_saved_states(path: Path, record: ProjectRecord,
                         saved_states, *, keep: int) -> ProjectRecord:
    """Publish newly server-confirmed states before pruning old ones."""
    by_frame = {item.frame: item for item in record.checkpoints
                if _verified_checkpoint(item)}
    for frame_value in sorted({int(value) for value in saved_states}):
        state_path = checkpoint_path(Path(record.project_root), frame_value)
        try:
            if not state_path.is_file() or state_path.stat().st_size <= 0:
                continue
            with gzip.open(state_path, "rb") as stream:
                if not stream.read(1):
                    continue
            size = state_path.stat().st_size
            digest = _sha256(state_path)
        except (OSError, EOFError):
            continue
        existing = by_frame.get(frame_value)
        if (existing is not None and existing.checkpoint_size == size
                and existing.checkpoint_sha256 == digest):
            continue
        by_frame[frame_value] = CheckpointRecord(
            frame=frame_value, project_id=record.project_id,
            identity=record.identity, created_at=time.time(),
            checkpoint_path=str(state_path.resolve()),
            checkpoint_size=size, checkpoint_sha256=digest)
    confirmed = tuple(sorted(
        by_frame.values(), key=lambda item: (item.frame, item.created_at)))
    state = (ProjectState.CHECKPOINT_CONFIRMED if confirmed
             else record.state)
    published = transition(
        path, record, state, checkpoints=confirmed,
        last_frame=max((item.frame for item in confirmed), default=0))
    keep = max(1, int(keep))
    if len(confirmed) <= keep:
        return published
    retained, stale = confirmed[-keep:], confirmed[:-keep]
    # Publish retention first. A crash can leave an extra old state, never a
    # metadata entry pointing to a state that was already removed.
    published = transition(
        path, published, published.state, checkpoints=tuple(retained))
    for item in stale:
        owned = owned_checkpoint_path(path, published, item)
        if owned is None:
            continue
        try:
            owned.unlink()
        except OSError:
            pass
    return published


def publish_checkpoint(cache_directory: Path, identity: RecoveryIdentity, *,
                       frame: int, project_id: str,
                       checkpoint_path: Path) -> CheckpointRecord:
    """Legacy helper retained for tests and metadata migration tooling."""
    checkpoint = Path(checkpoint_path).resolve()
    if not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    record = CheckpointRecord(
        frame=int(frame), project_id=str(project_id), identity=identity,
        created_at=time.time(), checkpoint_path=str(checkpoint),
        checkpoint_size=checkpoint.stat().st_size,
        checkpoint_sha256=_sha256(checkpoint))
    root = recovery_root(cache_directory, identity.scene_key)
    metadata = root / METADATA_NAME
    existing = load_records(metadata)
    records = [item for item in existing
               if not (item.frame == record.frame and
                       item.project_id == record.project_id)]
    records.append(record)
    records.sort(key=lambda item: (item.frame, item.created_at))
    _atomic_json(metadata, {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "records": [_record_dict(item) for item in records],
    })
    return record


def load_records(path: Path) -> tuple[CheckpointRecord, ...]:
    """Read either legacy checkpoint-only or project lifecycle metadata."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        stored_schema_version = int(raw["schema_version"])
        if stored_schema_version not in _READABLE_RECOVERY_SCHEMA_VERSIONS:
            return ()
        values = (raw["project"]["checkpoints"]
                  if "project" in raw else raw["records"])
        return tuple(record for record in (
            _record_from_dict(
                value, stored_schema_version=stored_schema_version)
            for value in values)
                     if _verified_checkpoint(record))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return ()


def compatibility(saved: RecoveryIdentity, current: RecoveryIdentity, *,
                  can_update_params: bool = False) -> Compatibility:
    scene_fields = (
        "scene_key", "export_uuids", "geometry_fingerprint",
        "topology_fingerprint", "frame_start", "frame_end", "fps",
        "collider_sampling", "solver_version", "protocol_version",
        "solver_schema_version", "recovery_schema_version",
        "solver_installation_id", "solver_release_tag")
    labels = {
        "scene_key": "Scene data changed",
        "export_uuids": "Object identity or role changed",
        "geometry_fingerprint": "Geometry changed",
        "topology_fingerprint": "Topology changed",
        "frame_start": "Frame range changed",
        "frame_end": "Frame range changed",
        "fps": "FPS changed",
        "collider_sampling": "Collider sampling changed",
        "solver_version": "Solver version changed",
        "protocol_version": "Protocol version changed",
        "solver_schema_version": "Solver schema changed",
        "solver_installation_id": "Solver installation changed",
        "solver_release_tag": "Solver release changed",
        "recovery_schema_version": "Recovery format changed",
    }
    for name in scene_fields:
        if getattr(saved, name) != getattr(current, name):
            return Compatibility(False, labels[name])
    if saved.param_key != current.param_key:
        if can_update_params:
            return Compatibility(True, "Parameters can be updated", True)
        return Compatibility(False, "Material or solver settings changed")
    return Compatibility(True, "Compatible")


def evaluate_resumable(path: Path,
                       current_identity: RecoveryIdentity | None = None, *,
                       can_update_params: bool = False) -> ResumeEligibility:
    """Single source of truth for resume eligibility.

    Reads and strictly verifies the durable metadata; never trusts in-memory
    flags or a state set duplicated by the caller.  Checkpoint integrity is
    re-authenticated on every call, so a truncated or replaced state file
    fails closed.
    """
    record = load_project(path)
    if record is None:
        return ResumeEligibility(
            available=False, resumable=False, compatible=None,
            state=None, project_id=None, latest_checkpoint_frame=0,
            checkpoint_count=0, generation=0, error="",
            reason="Recovery metadata is missing or invalid")
    checkpoints = record.checkpoints  # load_project verified these
    available = bool(record.state in _RESUMABLE_STATES and checkpoints)
    match = None
    compatible: bool | None = None
    if current_identity is not None:
        match = compatibility(
            record.identity, current_identity,
            can_update_params=can_update_params)
        compatible = match.compatible
    if not available:
        reason = "No verified resumable checkpoint is available"
    elif match is not None and not match.compatible:
        reason = match.reason
    else:
        reason = "Compatible"
    return ResumeEligibility(
        available=available,
        resumable=bool(available and (compatible is None or compatible)),
        compatible=compatible, state=record.state,
        project_id=record.project_id,
        latest_checkpoint_frame=max(
            (item.frame for item in checkpoints), default=0),
        checkpoint_count=len(checkpoints), generation=record.generation,
        error=record.error, reason=reason)


def reconcile_resumable(path: Path,
                        current_identity: RecoveryIdentity, *,
                        can_update_params: bool = False) \
        -> tuple[ResumeEligibility, bool]:
    """Normalize an eligible project to RESUMABLE when it is safe to do so.

    Used at the point where a resume is actually attempted (Bake start) and
    by failure paths, never by panel draws or the read-only evaluator.
    Returns ``(eligibility, promoted)``.
    """
    eligibility = evaluate_resumable(
        path, current_identity, can_update_params=can_update_params)
    if (not eligibility.resumable
            or eligibility.state is ProjectState.RESUMABLE):
        return eligibility, False
    record = load_project(path)
    if record is None:
        return eligibility, False
    published = transition(path, record, ProjectState.RESUMABLE,
                           error=record.error)
    return replace(
        eligibility, state=ProjectState.RESUMABLE,
        generation=published.generation), True


def apply_retention(path: Path, keep: int) -> tuple[Path, ...]:
    keep = max(1, int(keep))
    records = list(load_records(path))
    if len(records) <= keep:
        return ()
    records.sort(key=lambda item: (item.frame, item.created_at))
    stale, retained = records[:-keep], records[-keep:]
    _atomic_json(Path(path), {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "records": [_record_dict(item) for item in retained],
    })
    # Legacy checkpoint indexes do not carry a trusted project/server root.
    # Remove their metadata entries, but never treat their persisted absolute
    # paths as authority to delete a filesystem object.
    return ()


def clear_checkpoints(path: Path) -> tuple[Path, ...]:
    record = load_project(path)
    records = record.checkpoints if record is not None else load_records(path)
    removed = []
    for item in records:
        file_path = (owned_checkpoint_path(path, record, item)
                     if record is not None else None)
        if file_path is None:
            continue
        try:
            file_path.unlink()
            removed.append(file_path)
        except OSError:
            pass
    if record is not None:
        transition(path, record, ProjectState.ABANDONED, checkpoints=(),
                   error="Checkpoints cleared by user")
    else:
        try:
            Path(path).unlink()
        except OSError:
            pass
    return tuple(removed)
