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

RECOVERY_SCHEMA_VERSION = 3
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
        ProjectState.SAVED, ProjectState.FINISHED, ProjectState.FAILED,
        ProjectState.ABANDONED},
    ProjectState.CHECKPOINT_REQUESTED: {
        ProjectState.CHECKPOINT_CONFIRMED, ProjectState.SAVED,
        ProjectState.FAILED, ProjectState.ABANDONED},
    ProjectState.CHECKPOINT_CONFIRMED: {
        ProjectState.RUNNING, ProjectState.CHECKPOINT_REQUESTED,
        ProjectState.SAVED, ProjectState.RESUMABLE, ProjectState.FINISHED,
        ProjectState.FAILED},
    ProjectState.SAVED: {ProjectState.RESUMABLE, ProjectState.FAILED,
                         ProjectState.DELETED},
    ProjectState.RESUMABLE: {ProjectState.RESUMING, ProjectState.ABANDONED,
                             ProjectState.DELETED},
    ProjectState.RESUMING: {
        ProjectState.RUNNING, ProjectState.CHECKPOINT_REQUESTED,
        ProjectState.CHECKPOINT_CONFIRMED, ProjectState.SAVED,
        ProjectState.FINISHED, ProjectState.FAILED,
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
class PartialCacheRecord:
    object_uuid: str
    path: str
    frame_count: int
    file_size: int
    sha256: str
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
    partial_caches: tuple[PartialCacheRecord, ...] = ()
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


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """One verified answer shared by recovery UI and execution guards."""

    available: bool
    checkpoint: CheckpointRecord | None
    can_resume: bool
    reason: str
    record: ProjectRecord | None = None
    compatible: bool = False


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


def _gzip_is_complete(path: Path) -> bool:
    """Drain the stream so gzip CRC and footer validation must succeed."""
    produced = False
    with gzip.open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            produced = produced or bool(chunk)
    return produced


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
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


def _identity_from_dict(value: dict) -> RecoveryIdentity:
    data = dict(value)
    data["export_uuids"] = tuple(str(item) for item in data["export_uuids"])
    data["collider_sampling"] = tuple(
        (str(name), int(samples))
        for name, samples in data["collider_sampling"])
    return RecoveryIdentity(**data)


def _record_dict(record: CheckpointRecord) -> dict:
    value = asdict(record)
    value["identity"] = _identity_dict(record.identity)
    return value


def _record_from_dict(value: dict) -> CheckpointRecord:
    return CheckpointRecord(
        frame=int(value["frame"]), project_id=str(value["project_id"]),
        identity=_identity_from_dict(value["identity"]),
        created_at=float(value["created_at"]),
        checkpoint_path=str(value["checkpoint_path"]),
        checkpoint_size=int(value["checkpoint_size"]),
        checkpoint_sha256=str(value["checkpoint_sha256"]),
        integrity=str(value.get("integrity", "VERIFIED")))


def _partial_record_dict(record: PartialCacheRecord) -> dict:
    return asdict(record)


def _partial_record_from_dict(value: dict) -> PartialCacheRecord:
    return PartialCacheRecord(
        object_uuid=str(value["object_uuid"]), path=str(value["path"]),
        frame_count=int(value["frame_count"]), file_size=int(value["file_size"]),
        sha256=str(value["sha256"]),
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
            return _gzip_is_complete(path)
        return True
    except (OSError, EOFError, gzip.BadGzipFile):
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
            "partial_caches": [
                _partial_record_dict(item) for item in record.partial_caches],
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
        if int(raw["schema_version"]) != RECOVERY_SCHEMA_VERSION:
            return None
        value = raw["project"]
        checkpoints = tuple(
            _record_from_dict(item) for item in value["checkpoints"])
        if verify_checkpoints:
            checkpoints = tuple(item for item in checkpoints
                                if _verified_checkpoint(item))
        record = ProjectRecord(
            project_id=str(value["project_id"]),
            state=ProjectState(str(value["state"])),
            identity=_identity_from_dict(value["identity"]),
            server_data_root=str(value["server_data_root"]),
            project_root=str(value["project_root"]),
            checkpoints=checkpoints,
            partial_pc2=tuple(
                (str(uuid), str(path))
                for uuid, path in value.get("partial_pc2", ())),
            partial_caches=tuple(
                _partial_record_from_dict(item)
                for item in value.get("partial_caches", ())),
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
               partial_caches: tuple[PartialCacheRecord, ...] | None = None,
               error: str = "") -> ProjectRecord:
    current = load_project(path, verify_checkpoints=False)
    if (current is not None and current.project_id == record.project_id
            and current.identity == record.identity
            and current.generation > record.generation):
        record = current
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
        partial_caches=(
            record.partial_caches if partial_caches is None
            else tuple(sorted(
                partial_caches, key=lambda item: item.object_uuid))),
        error=str(error), generation=record.generation + 1)
    return publish_project(path, updated)


def publish_partial_caches(
        path: Path, entries: tuple[tuple[str, Path, int], ...]) \
        -> ProjectRecord:
    """Authenticate stable, frame-aligned partial streams in project metadata."""
    record = load_project(path)
    if record is None:
        raise ValueError("recovery metadata is missing or invalid")
    expected_paths = dict(record.partial_pc2)
    verified = []
    for object_uuid, raw_path, frame_count in entries:
        cache_path = Path(raw_path).resolve()
        if (str(cache_path)
                != str(Path(expected_paths.get(object_uuid, "")).resolve())):
            raise ValueError(
                f"partial cache path changed for {object_uuid}")
        before = cache_path.stat()
        if frame_count <= 0 or before.st_size <= 0:
            raise ValueError("partial cache contains no complete frames")
        digest = _sha256(cache_path)
        after = cache_path.stat()
        if (before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns):
            raise ValueError("partial cache changed during verification")
        verified.append(PartialCacheRecord(
            object_uuid=str(object_uuid), path=str(cache_path),
            frame_count=int(frame_count), file_size=after.st_size,
            sha256=digest))
    if {item.object_uuid for item in verified} != set(expected_paths):
        raise ValueError("partial cache set is incomplete")
    return transition(
        path, record, record.state, partial_caches=tuple(verified))


def verified_partial_cache(record: ProjectRecord, object_uuid: str,
                           expected_path: Path) \
        -> PartialCacheRecord | None:
    """Return an authenticated partial record only while its bytes are stable."""
    candidate = next(
        (item for item in record.partial_caches
         if item.object_uuid == object_uuid), None)
    if candidate is None or candidate.integrity != "VERIFIED":
        return None
    path = Path(expected_path).resolve()
    try:
        if (path != Path(candidate.path).resolve()
                or not path.is_file()
                or candidate.frame_count <= 0
                or candidate.file_size != path.stat().st_size
                or candidate.sha256 != _sha256(path)):
            return None
    except OSError:
        return None
    return candidate


def checkpoint_path(project_root: Path, frame: int) -> Path:
    return (Path(project_root) / "session" / "output"
            / f"state_{int(frame)}.bin.gz")


def discover_checkpoint_frames(project_root: Path) -> tuple[int, ...]:
    """Return solver checkpoint filenames without treating them as verified."""
    output = Path(project_root) / "session" / "output"
    frames = set()
    try:
        candidates = tuple(output.glob("state_*.bin.gz"))
    except OSError:
        return ()
    for candidate in candidates:
        name = candidate.name
        try:
            frame = int(name[len("state_"):-len(".bin.gz")])
        except (TypeError, ValueError):
            continue
        if frame >= 0:
            frames.add(frame)
    return tuple(sorted(frames))


def _checkpoint_record(record: ProjectRecord, frame: int) \
        -> CheckpointRecord | None:
    path = checkpoint_path(Path(record.project_root), frame)
    try:
        before = path.stat()
        if not path.is_file() or before.st_size <= 0:
            return None
        if not _gzip_is_complete(path):
            return None
        digest = _sha256(path)
        after = path.stat()
        if (after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns):
            return None
    except (OSError, EOFError, gzip.BadGzipFile):
        return None
    return CheckpointRecord(
        frame=frame, project_id=record.project_id,
        identity=record.identity, created_at=time.time(),
        checkpoint_path=str(path.resolve()),
        checkpoint_size=after.st_size, checkpoint_sha256=digest)


def confirm_saved_states(path: Path, record: ProjectRecord,
                         saved_states, *, keep: int) -> ProjectRecord:
    """Publish complete states found in solver status or its output directory."""
    current = load_project(path, verify_checkpoints=False)
    if (current is not None and current.project_id == record.project_id
            and current.identity == record.identity
            and current.generation > record.generation):
        record = current
    by_frame = {item.frame: item for item in record.checkpoints
                if _verified_checkpoint(item)}
    reported = {
        int(value) for value in saved_states
        if isinstance(value, (int, float)) and int(value) >= 0}
    reported.update(discover_checkpoint_frames(record.project_root))
    for frame_value in sorted(reported):
        candidate = _checkpoint_record(record, frame_value)
        if candidate is None:
            continue
        existing = by_frame.get(frame_value)
        if (existing is not None
                and existing.checkpoint_size == candidate.checkpoint_size
                and existing.checkpoint_sha256 == candidate.checkpoint_sha256):
            continue
        by_frame[frame_value] = candidate
    confirmed = tuple(sorted(
        by_frame.values(), key=lambda item: (item.frame, item.created_at)))
    confirming_states = {
        ProjectState.RUNNING, ProjectState.RESUMING,
        ProjectState.CHECKPOINT_REQUESTED,
    }
    state = (
        ProjectState.CHECKPOINT_CONFIRMED
        if confirmed and record.state in confirming_states
        else record.state)
    last_frame = max((item.frame for item in confirmed), default=0)
    if (state is record.state and confirmed == record.checkpoints
            and last_frame == record.last_frame):
        published = record
    else:
        published = transition(
            path, record, state, checkpoints=confirmed,
            last_frame=last_frame)
    keep = max(1, int(keep))
    if len(confirmed) <= keep:
        return published
    retained, stale = confirmed[-keep:], confirmed[:-keep]
    # Publish retention first. A crash can leave an extra old state, never a
    # metadata entry pointing to a state that was already removed.
    published = transition(
        path, published, published.state, checkpoints=tuple(retained),
        last_frame=max(item.frame for item in retained))
    for item in stale:
        try:
            Path(item.checkpoint_path).unlink()
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
        if int(raw["schema_version"]) != RECOVERY_SCHEMA_VERSION:
            return ()
        values = (raw["project"]["checkpoints"]
                  if "project" in raw else raw["records"])
        return tuple(record for record in map(_record_from_dict, values)
                     if _verified_checkpoint(record))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return ()


def compatibility(saved: RecoveryIdentity, current: RecoveryIdentity, *,
                  can_update_params: bool = False) -> Compatibility:
    scene_fields = (
        "export_uuids", "geometry_fingerprint", "topology_fingerprint",
        "frame_start", "frame_end", "fps",
        "collider_sampling", "solver_version", "protocol_version",
        "solver_schema_version", "recovery_schema_version",
        "solver_installation_id", "solver_release_tag", "scene_key")
    labels = {
        "scene_key": "Animated Collider or exported Scene data changed",
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
        return Compatibility(
            False, "Pin, force, material, FPS/Time Scale, or solver "
            "parameters changed")
    return Compatibility(True, "Compatible")


def assess_recovery(path: Path, *,
                    current_identity: RecoveryIdentity | None = None,
                    busy: bool = False) -> RecoveryAssessment:
    """Verify recovery bytes, identity, lifecycle and runtime availability."""
    path = Path(path)
    unverified = load_project(path, verify_checkpoints=False)
    if unverified is None:
        reason = (
            "Recovery metadata file is missing"
            if not path.is_file()
            else "Recovery metadata could not be loaded")
        return RecoveryAssessment(False, None, False, reason)
    verified = load_project(path)
    if verified is None:
        return RecoveryAssessment(
            False, None, False,
            "Recovery metadata could not be loaded")
    checkpoints = tuple(sorted(
        verified.checkpoints, key=lambda item: (item.frame, item.created_at)))
    if not checkpoints:
        reason = (
            "Recovery checkpoint file is missing, incomplete, or damaged"
            if unverified.checkpoints
            else "No verified recovery checkpoint is available")
        return RecoveryAssessment(False, None, False, reason, verified)
    checkpoint = checkpoints[-1]
    if current_identity is not None:
        match = compatibility(verified.identity, current_identity)
        if not match.compatible:
            return RecoveryAssessment(
                True, checkpoint, False, match.reason, verified)
    if verified.state not in {ProjectState.RESUMABLE, ProjectState.FAILED}:
        return RecoveryAssessment(
            True, checkpoint, False,
            f"Recovery project state is {verified.state.value.title()}",
            verified, True)
    if busy:
        return RecoveryAssessment(
            True, checkpoint, False,
            "A solver or Bake operation is still running", verified, True)
    invalid_newer = any(
        item.frame > checkpoint.frame for item in unverified.checkpoints
        if item not in checkpoints)
    reason = (
        f"Newest checkpoint is invalid; frame {checkpoint.frame} is verified"
        if invalid_newer else
        f"Verified checkpoint at frame {checkpoint.frame}")
    return RecoveryAssessment(
        True, checkpoint, True, reason, verified, True)


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
    removed = []
    for record in stale:
        file_path = Path(record.checkpoint_path)
        try:
            file_path.unlink()
            removed.append(file_path)
        except OSError:
            pass
    return tuple(removed)


def clear_checkpoints(path: Path) -> tuple[Path, ...]:
    record = load_project(path)
    records = record.checkpoints if record is not None else load_records(path)
    removed = []
    for item in records:
        file_path = Path(item.checkpoint_path)
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
