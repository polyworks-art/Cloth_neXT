# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure solver session service: one real PPF run from upload to frames.

No ``bpy`` anywhere in this module (enforced by tests). The Blender side
hands in an immutable :class:`SessionScene`; this service starts or connects
the solver, uploads, builds, simulates, incrementally fetches and validates
result frames, emits :class:`SessionEvent` progress, honors cancellation,
cleans up its unique project, and stops only processes it started itself.

Process management, health checks, ownership rules, error taxonomy, and
logging all reuse the existing Phase-2 building blocks; no second process
implementation exists here.
"""

from __future__ import annotations

import logging
import json
import threading
import time
import uuid as uuid_module
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import numpy as np

from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord
from ..core.logging import get_logger, log_with_context
from ..ppf import results, wire
from ..ppf.health import start_owned_and_wait
from ..ppf.layout import BundledSolverLayout
from ..ppf.models import ConnectionOwnership
from ..ppf.process import SolverProcessConfig, SolverProcessManager
from ..ppf.resolver import ResolvedSolver
from ..ppf.transport import TransportConfig
from .. import recovery
from ..updater.health_runner import bundle_root_for, free_port

# Wire status tokens (crates/ppf-cts-server, verified at pinned 7193f158).
STATUS_NO_DATA = "NO_DATA"
STATUS_NO_BUILD = "NO_BUILD"
STATUS_BUILDING = "BUILDING"
STATUS_READY = "READY"
STATUS_RESUMABLE = "RESUMABLE"
STATUS_FAILED = "FAILED"
STATUS_BUSY = "BUSY"
STATUS_SAVE_AND_QUIT = "SAVE_AND_QUIT"

_POLL_INTERVAL = 0.25
_MAX_VIOLATION_SIDECAR_BYTES = 4 * 1024 * 1024
_MAX_VIOLATION_PREVIEWS = 256
_VIOLATION_SIDECAR_CONFIRM_TIMEOUT = 1.0

_SOLVER_METRICS = {
    "contacts": "advance.num_contact.out",
    "newton": "advance.newton_steps.out",
    "iterations": "advance.iter.out",
}


class RecoveryOutcomeKind(str, Enum):
    SAVED = "SAVED"
    EXISTING_PRESERVED = "EXISTING_PRESERVED"
    NOT_ENABLED = "NOT_ENABLED"
    NOT_AVAILABLE_YET = "NOT_AVAILABLE_YET"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """Structured result of a recovery checkpoint attempt on cancellation."""
    kind: RecoveryOutcomeKind
    """Unambiguous category used by the Blender UI."""
    checkpoint_saved: bool
    """True if a verified checkpoint was confirmed and the project is resumable."""
    artist_message: str
    """Short user-facing message for the UI (e.g., 'Recovery checkpoint saved')."""
    technical_reason: str
    """Detailed reason for logging/debugging (empty on success)."""
    state_before: str
    """Server status when cancellation was requested (e.g., 'BUSY', 'BUILDING')."""
    saved_states: tuple[int, ...]
    """Frames with confirmed saved states, if any."""
    timed_out: bool = False
    """True if the attempt timed out waiting for confirmation."""

    @property
    def resumable(self) -> bool:
        return self.checkpoint_saved
def _tail_numeric_metric(path: Path) -> int | None:
    """Read one live PPF metric without loading its growing history."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            if size <= 0:
                return None
            stream.seek(max(0, size - 4096))
            lines = stream.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            return int(float(fields[-1]))
        except ValueError:
            continue
    return None


class SessionCancelled(Exception):
    """The run was cancelled cooperatively; not an error."""

    def __init__(self, *, resumable: bool = False,
                 recovery_outcome: RecoveryOutcome | None = None) -> None:
        super().__init__("solver session cancelled")
        self.resumable = bool(resumable)
        self.recovery_outcome = recovery_outcome


@dataclass(frozen=True, slots=True)
class RecoveryOptions:
    enabled: bool
    metadata_path: Path
    identity: recovery.RecoveryIdentity
    server_data_root: Path
    resume: bool = False
    keep_saved_states: int = 3
    save_on_cancel: bool = True
    keep_on_finish: bool = False
    completed_solver_frames: tuple[int, ...] = ()
    partial_pc2: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SessionDeformable:
    name: str
    uuid: str
    vertex_count: int
    deformable_type: str = "SHELL"
    world_matrix: tuple | None = None


@dataclass(frozen=True, slots=True)
class SessionScene:
    """Immutable scene export handed from Blender's main thread."""

    project_name: str
    cloth_name: str
    cloth_uuid: str
    cloth_vertex_count: int
    collider_name: str
    collider_uuid: str
    frame_count: int  # Blender frames 1..frame_count
    data_payload: bytes | Path
    param_payload: bytes
    data_hash: str
    param_hash: str
    deformable_type: str = "SHELL"
    deformable_world_matrix: tuple | None = None
    deformables: tuple[SessionDeformable, ...] = ()

    @property
    def dynamic_objects(self) -> tuple[SessionDeformable, ...]:
        if self.deformables:
            return self.deformables
        return (SessionDeformable(
            self.cloth_name, self.cloth_uuid, self.cloth_vertex_count,
            self.deformable_type, self.deformable_world_matrix),)

    @property
    def solver_frame_count(self) -> int:
        """Frames the solver produces (vert_1..vert_N): Blender N -> N-1."""
        return self.frame_count - 1


def new_project_name() -> str:
    """Unique, server-safe project key; never a blend-file or object name."""
    return f"clothnext_{uuid_module.uuid4().hex[:12]}"


@dataclass(frozen=True, slots=True)
class SessionEvent:
    phase: str
    message: str
    frame_current: int | None = None
    frame_total: int | None = None
    indeterminate: bool = False
    process_id: int | None = None
    solver_mode: str = ""
    package_version: str | None = None
    protocol_version: str | None = None
    schema_version: str | None = None
    host: str = ""
    port: int = 0
    activity_code: str = ""


@dataclass(frozen=True, slots=True)
class SolverFrame:
    """One validated solver frame split into original dynamic objects."""

    solver_frame: int  # 1-based solver frame index (vert_<N>.bin)
    positions_solver_world: object
    positions_by_uuid: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class SessionDiagnostics:
    run_id: str = field(default_factory=lambda: uuid_module.uuid4().hex)
    project_name: str = ""
    solver_mode: str = ""
    installation_id: str | None = None
    official_release_tag: str | None = None
    host: str = ""
    port: int = 0
    process_id: int | None = None
    package_version: str | None = None
    protocol_version: str | None = None
    schema_version: str | None = None
    upload_id: str = ""
    data_hash: str = ""
    param_hash: str = ""
    status_transitions: list[str] = field(default_factory=list)
    fetched_frames: list[int] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    cache_events: dict[str, str] = field(default_factory=dict)
    stdout_tail: tuple[str, ...] = ()
    stderr_tail: tuple[str, ...] = ()
    contact_peak: int = 0
    contact_last: int = 0
    contact_samples: int = 0
    cancelled: bool = False
    bytes_transferred: int = 0
    upload_data_bytes: int = 0
    upload_param_bytes: int = 0
    upload_total_bytes: int = 0

    def note_status(self, status: str) -> None:
        if not self.status_transitions or self.status_transitions[-1] != status:
            self.status_transitions.append(status)


def _session_error(message: str, technical: str, *,
                   category: ErrorCategory = ErrorCategory.SIMULATION,
                   action: str = "Inspect the Cloth NeXt log and the solver "
                                 "stderr tail, then retry.") -> ClothNextError:
    return ClothNextError(ErrorRecord.create(
        category=category, user_message=message, technical_message=technical,
        recommended_action=action, recoverable=True))


class SolverSession:
    """Drives one complete vertical-slice run against a real PPF server."""

    def __init__(self, *, resolved: ResolvedSolver,
                 scene: SessionScene, work_directory: Path,
                 external_address: wire.ServerAddress | None = None,
                 transport: TransportConfig | None = None,
                 emit: Callable[[SessionEvent], None] | None = None,
                 cancel_event: threading.Event | None = None,
                 frame_sink: Callable[[SolverFrame], None] | None = None,
                 poll_interval: float = _POLL_INTERVAL,
                 build_timeout: float = 600.0,
                 simulate_timeout: float = 600.0,
                 recovery_options: RecoveryOptions | None = None) -> None:
        self.resolved = resolved
        self.scene = scene
        self.work_directory = work_directory
        self.transport = transport or TransportConfig(connect_timeout=5.0,
                                                      read_timeout=30.0)
        self._emit = emit or (lambda event: None)
        self._cancel = cancel_event or threading.Event()
        self._frame_sink = frame_sink or (lambda frame: None)
        self._poll_interval = poll_interval
        self._build_timeout = build_timeout
        self._simulate_timeout = simulate_timeout
        self._recovery = recovery_options
        self._recovery_record: recovery.ProjectRecord | None = None
        self._known_saved_states: tuple[int, ...] = ()
        self._manager: SolverProcessManager | None = None
        self._address: wire.ServerAddress | None = external_address
        self._logger = get_logger("solver.session")
        self._indices_by_uuid: dict[str, np.ndarray] = {}
        self._surface_maps_by_uuid: dict[str, results.SurfaceMap] = {}
        self.diagnostics = SessionDiagnostics(project_name=scene.project_name,
                                              solver_mode=resolved.mode.name,
                                              installation_id=(
                                                  resolved.installation_id),
                                              official_release_tag=(
                                                  resolved.installation.official_release_tag
                                                  if resolved.installation
                                                  else None),
                                              data_hash=scene.data_hash,
                                              param_hash=scene.param_hash)
        if resolved.ownership is ConnectionOwnership.EXTERNAL_SERVER:
            if external_address is None:
                raise ValueError("an external server requires an address")
        elif resolved.executable_path is None:
            raise ValueError("an owned solver requires an executable path")

    # -- helpers ------------------------------------------------------------

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise SessionCancelled()

    def _event(self, phase: str, message: str, **kwargs) -> None:
        self._emit(SessionEvent(phase=phase, message=message, **kwargs))

    def _metadata_event(self) -> None:
        self._event("RUNTIME_METADATA", "Solver runtime connected",
                    process_id=self.diagnostics.process_id,
                    solver_mode=self.diagnostics.solver_mode,
                    package_version=self.diagnostics.package_version,
                    protocol_version=self.diagnostics.protocol_version,
                    schema_version=self.diagnostics.schema_version,
                    host=self.diagnostics.host, port=self.diagnostics.port)

    def _status(self) -> dict:
        assert self._address is not None
        response = wire.send_tcmd(self._address, self.transport,
                                  self.scene.project_name)
        status = str(response.get("status", ""))
        self.diagnostics.note_status(status)
        self._sync_checkpoints(response)
        return response

    def _runtime_activity(self) -> tuple[str, str]:
        server_root = self._server_data_root()
        metric_root = (server_root /
                       self.scene.project_name / "session" / "output" /
                       "data")
        values = {name: _tail_numeric_metric(metric_root / filename)
                  for name, filename in _SOLVER_METRICS.items()}
        if any(value is not None for value in values.values()):
            contacts = values["contacts"]
            if contacts is not None:
                self.diagnostics.contact_last = contacts
                self.diagnostics.contact_peak = max(
                    self.diagnostics.contact_peak, contacts)
                self.diagnostics.contact_samples += 1
            parts = []
            if contacts is not None:
                parts.append(f"{contacts:,} contacts")
            if values["newton"] is not None:
                parts.append(f"Newton {values['newton']}")
            if values["iterations"] is not None:
                parts.append(f"{values['iterations']:,} linear iterations")
            return "SOLVING_CONSTRAINTS", "Solver · " + " · ".join(parts)
        if self._manager is None:
            return "", ""
        poll = self._manager.poll()
        return poll.activity_code, poll.activity_message

    def _server_data_root(self) -> Path:
        """Return the run's solver-data root independently of recovery."""
        return (
            self._recovery.server_data_root
            if self._recovery is not None
            else self.work_directory / "server-data")

    def _request(self, request: str) -> dict:
        assert self._address is not None
        response = wire.send_tcmd(self._address, self.transport,
                                  self.scene.project_name, request)
        self.diagnostics.note_status(str(response.get("status", "")))
        return response

    def _capture_process_tails(self) -> None:
        if self._manager is not None:
            poll = self._manager.poll()
            self.diagnostics.stdout_tail = poll.stdout_tail
            self.diagnostics.stderr_tail = poll.stderr_tail
            self.diagnostics.contact_peak = poll.contact_peak
            self.diagnostics.contact_last = poll.contact_last
            self.diagnostics.contact_samples = poll.contact_samples

    def _owned_connection_error(self, exc: ClothNextError) -> ClothNextError:
        """Replace opaque socket failures with owned-process evidence."""
        executable = self.resolved.executable_path
        worker = (None if executable is None else
                  executable.with_name("ppf-contact-solver.exe"))
        if worker is not None and not worker.is_file():
            return ClothNextError(ErrorRecord.create(
                category=ErrorCategory.SOLVER_INSTALLATION,
                user_message=(
                    "The native solver worker is missing. Security software "
                    "may have quarantined it."),
                technical_message=(
                    f"native solver worker disappeared during the Bake: "
                    f"{worker}; original_error={exc.record.technical_message}"),
                recommended_action=(
                    "Restore or reinstall the verified solver, allow its "
                    "installation folder in the security software, then "
                    "retry the Bake."),
                recoverable=True,
                context={"worker_path": str(worker)}))
        if self._manager is None:
            return exc
        poll = self._manager.poll()
        self.diagnostics.stdout_tail = poll.stdout_tail
        self.diagnostics.stderr_tail = poll.stderr_tail
        if not poll.running:
            return self._manager.early_exit_error(poll)
        record = exc.record
        return ClothNextError(ErrorRecord.create(
            category=record.category,
            user_message=record.user_message,
            technical_message=(f"{record.technical_message}; "
                f"owned_process_id={poll.process_id}; "
                f"stdout_tail={poll.stdout_tail}; "
                f"stderr_tail={poll.stderr_tail}; "
                f"progress_tail={poll.progress.tail}"),
            recommended_action=record.recommended_action,
            recoverable=record.recoverable,
            context={"process_id": poll.process_id,
                     "exit_code": poll.exit_code}))

    def _fail_from_status(self, response: dict, phase: str) -> ClothNextError:
        self._capture_process_tails()
        error_text = str(response.get("error", "") or "no server error text")
        parsed_violations = []
        raw_violations = response.get("violations", ())
        if isinstance(raw_violations, (list, tuple)):
            for item in raw_violations:
                try:
                    value = json.loads(item) if isinstance(item, str) else item
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    parsed_violations.append(value)
        if not parsed_violations:
            parsed_violations = list(self._load_build_violation_sidecar(
                phase=phase))
        if not parsed_violations and phase == "simulating":
            parsed_violations = list(
                self._load_runtime_intersection_sidecar(phase=phase))
        error = _session_error(
            f"The solver reported a failure while {phase}.",
            f"server status FAILED during {phase}: {error_text}; "
            f"contacts(last={self.diagnostics.contact_last}, "
            f"peak={self.diagnostics.contact_peak}, "
            f"samples={self.diagnostics.contact_samples}); "
            f"stdout_tail={self.diagnostics.stdout_tail}; "
            f"stderr_tail={self.diagnostics.stderr_tail}")
        if parsed_violations:
            log_with_context(self._logger, logging.ERROR,
                "Solver returned structured build violations", {
                    "project": self.scene.project_name,
                    "phase": phase,
                    "violation_count": len(parsed_violations),
                    "preview_count": min(10, len(parsed_violations)),
                })
            return ClothNextError(
                error.record, violations=tuple(parsed_violations))
        return error

    def _load_runtime_intersection_sidecar(
            self, *, phase: str) -> tuple[dict, ...]:
        """Capture the failed solver step before project cleanup removes it."""
        server_root = self._server_data_root().resolve()
        project_root = (server_root / self.scene.project_name).resolve()
        try:
            project_root.relative_to(server_root)
        except ValueError:
            return ()
        path = (
            project_root / "session" / "output"
            / "intersection_records.json")
        deadline = time.monotonic() + _VIOLATION_SIDECAR_CONFIRM_TIMEOUT
        while not path.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            size = path.stat().st_size
            if size <= 0 or size > _MAX_VIOLATION_SIDECAR_BYTES:
                return ()
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError,
                json.JSONDecodeError, ValueError):
            return ()
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            return ()
        violations = []
        for record in records:
            if not isinstance(record, dict):
                continue
            triangles = []
            for key in ("positions0", "positions1"):
                positions = record.get(key)
                if isinstance(positions, list) and len(positions) == 3:
                    triangles.append(positions)
            if not triangles:
                continue
            violations.append({
                "type": str(record.get("type", "runtime_intersection")),
                "tris": triangles,
                "detection_method": "SOLVER_RUNTIME_RECORD",
                "runtime_elements": [
                    record.get("elem0"), record.get("elem1")],
            })
        previews = tuple(violations[:_MAX_VIOLATION_PREVIEWS])
        if previews:
            log_with_context(
                self._logger, logging.ERROR,
                "Loaded runtime intersection records", {
                    "project": self.scene.project_name,
                    "phase": phase,
                    "path": str(path),
                    "record_count": len(records),
                    "preview_count": len(previews),
                })
        return previews

    def _load_build_violation_sidecar(self, *, phase: str) -> tuple[dict, ...]:
        """Read the managed frontend's structured build-error handoff.

        The pinned server does not expose this payload in its status response.
        Its build worker writes it beneath the already-known project root
        instead, so this is the authoritative local fallback for managed and
        other filesystem-visible solver installations.
        """
        server_root = self._server_data_root().resolve()
        project_root = (server_root / self.scene.project_name).resolve()
        try:
            project_root.relative_to(server_root)
        except ValueError:
            log_with_context(
                self._logger, logging.ERROR,
                "Rejected build violation sidecar outside server data root", {
                    "project": self.scene.project_name,
                    "phase": phase,
                    "server_data_root": str(server_root),
                    "project_root": str(project_root),
                })
            return ()
        path = project_root / "build_violations.json"
        mirror = server_root / (
            f"{self.scene.project_name}.build_violations.json")
        deadline = time.monotonic() + _VIOLATION_SIDECAR_CONFIRM_TIMEOUT
        while not path.is_file() and not mirror.is_file():
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        if not path.is_file() and mirror.is_file():
            path = mirror
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return ()
        except OSError as exc:
            log_with_context(
                self._logger, logging.WARNING,
                "Could not inspect build violation sidecar", {
                    "project": self.scene.project_name,
                    "phase": phase, "path": str(path), "reason": str(exc),
                })
            return ()
        if size <= 0 or size > _MAX_VIOLATION_SIDECAR_BYTES:
            log_with_context(
                self._logger, logging.WARNING,
                "Rejected invalid build violation sidecar size", {
                    "project": self.scene.project_name,
                    "phase": phase, "path": str(path), "size": size,
                    "maximum": _MAX_VIOLATION_SIDECAR_BYTES,
                })
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            log_with_context(
                self._logger, logging.WARNING,
                "Could not parse build violation sidecar", {
                    "project": self.scene.project_name,
                    "phase": phase, "path": str(path), "reason": str(exc),
                })
            return ()
        raw = payload.get("violations") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            log_with_context(
                self._logger, logging.WARNING,
                "Rejected malformed build violation sidecar", {
                    "project": self.scene.project_name,
                    "phase": phase, "path": str(path),
                })
            return ()
        valid = tuple(item for item in raw if isinstance(item, dict))
        previews = valid[:_MAX_VIOLATION_PREVIEWS]
        log_with_context(
            self._logger, logging.ERROR,
            "Loaded structured build violations from solver sidecar", {
                "project": self.scene.project_name,
                "phase": phase, "path": str(path),
                "violation_count": len(valid),
                "preview_count": len(previews),
            })
        return previews

    # -- lifecycle ----------------------------------------------------------

    def _recovery_start(self) -> None:
        options = self._recovery
        if options is None or not options.enabled:
            return
        path = options.metadata_path
        recovery.cleanup_temporary_files(path.parent)
        if options.resume:
            record = recovery.load_project(path)
            if record is None:
                raise _session_error(
                    "The recovery project is no longer available.",
                    f"invalid recovery metadata at {path}")
            match = recovery.compatibility(record.identity, options.identity)
            if not match.compatible:
                raise _session_error(
                    "The saved Bake is not compatible with this scene.",
                    match.reason)
            if record.project_id != self.scene.project_name:
                raise _session_error(
                    "The recovery project identity does not match.",
                    f"metadata project {record.project_id!r}, current "
                    f"{self.scene.project_name!r}")
            self._recovery_record = recovery.transition(
                path, record, recovery.ProjectState.RESUMING)
            return
        project_root = options.server_data_root / self.scene.project_name
        self._recovery_record = recovery.create_project(
            path, project_id=self.scene.project_name,
            identity=options.identity,
            server_data_root=options.server_data_root,
            project_root=project_root,
            partial_pc2=options.partial_pc2)
        self._recovery_record = recovery.transition(
            path, self._recovery_record, recovery.ProjectState.RUNNING)

    @staticmethod
    def _saved_states(response: dict) -> tuple[int, ...]:
        values = response.get("saved_states", ())
        if not isinstance(values, (list, tuple)):
            return ()
        return tuple(sorted({
            int(value) for value in values
            if isinstance(value, int) and value >= 0}))

    def _sync_checkpoints(self, response: dict) -> None:
        options, record = self._recovery, self._recovery_record
        if options is None or record is None:
            return
        saved = self._saved_states(response)
        self._known_saved_states = saved
        self._recovery_record = recovery.confirm_saved_states(
            options.metadata_path, record, saved,
            keep=options.keep_saved_states)

    def _start_owned_solver(self) -> None:
        executable = self.resolved.executable_path
        assert executable is not None
        worker = executable.with_name("ppf-contact-solver.exe")
        if not worker.is_file():
            raise ClothNextError(ErrorRecord.create(
                category=ErrorCategory.SOLVER_INSTALLATION,
                user_message=(
                    "The native solver worker is missing. Security software "
                    "may have quarantined it."),
                technical_message=f"native solver worker is missing: {worker}",
                recommended_action=(
                    "Restore or reinstall the verified solver, allow its "
                    "installation folder in the security software, then "
                    "retry the Bake."),
                recoverable=True,
                context={"worker_path": str(worker)}))
        root = bundle_root_for(executable)
        layout = BundledSolverLayout.from_root(root)
        server_data = self._server_data_root()
        server_data.mkdir(parents=True, exist_ok=True)
        environment = dict(layout.process_environment())
        # Pin the per-project server data below our own work directory so
        # the run's cache never lands in unrelated user locations.
        environment["PPF_CTS_DATA_ROOT"] = str(server_data)
        port = free_port()
        config = SolverProcessConfig(
            executable_path=executable,
            working_directory=root,
            host="127.0.0.1",
            port=port,
            startup_timeout=120.0,
            connect_timeout=self.transport.connect_timeout,
            read_timeout=self.transport.read_timeout,
            ownership_mode=ConnectionOwnership.OWNED_PROCESS,
            environment=tuple(sorted(environment.items())),
        )
        self._manager = SolverProcessManager(config)
        health = start_owned_and_wait(self._manager, self.scene.project_name)
        poll = self._manager.poll()
        self.diagnostics.host, self.diagnostics.port = config.host, config.port
        self.diagnostics.process_id = poll.process_id
        self.diagnostics.package_version = health.package_version
        self.diagnostics.protocol_version = health.protocol_version
        self.diagnostics.schema_version = health.schema_version
        self._address = wire.ServerAddress(config.host, config.port)

    def _upload(self) -> None:
        assert self._address is not None
        def payload_size(payload) -> int:
            if isinstance(payload, (str, Path)):
                return Path(payload).stat().st_size
            return len(payload)

        data_bytes = payload_size(self.scene.data_payload)
        param_bytes = payload_size(self.scene.param_payload)
        total_bytes = data_bytes + param_bytes
        self.diagnostics.upload_data_bytes = data_bytes
        self.diagnostics.upload_param_bytes = param_bytes
        self.diagnostics.upload_total_bytes = total_bytes
        self._event(
            "UPLOADING",
            f"Uploading scene · {total_bytes / (1024 * 1024):.1f} MiB",
            indeterminate=True)
        log_with_context(
            self._logger, logging.INFO, "Uploading solver payloads", {
                "project": self.scene.project_name,
                "data_bytes": data_bytes,
                "param_bytes": param_bytes,
                "total_bytes": total_bytes,
                "upload_write_timeout_seconds":
                    self.transport.upload_write_timeout,
            })
        wire.upload_atomic(self._address, self.transport,
                           project_name=self.scene.project_name,
                           data_payload=self.scene.data_payload,
                           param_payload=self.scene.param_payload,
                           data_hash=self.scene.data_hash,
                           param_hash=self.scene.param_hash)
        response = self._status()
        upload_id = str(response.get("upload_id", ""))
        if not upload_id:
            raise _session_error("The solver did not acknowledge the upload.",
                                 "status has no upload_id after upload_atomic")
        for label, expected, key in (("data", self.scene.data_hash, "data_hash"),
                                     ("param", self.scene.param_hash,
                                      "param_hash")):
            echoed = str(response.get(key, ""))
            if echoed != expected:
                raise _session_error(
                    "The solver acknowledged different payloads than were sent.",
                    f"{label} hash mismatch after upload: sent {expected}, "
                    f"server reports {echoed!r}")
        data_state = str(response.get("data", ""))
        if data_state == STATUS_NO_DATA:
            raise _session_error("The upload did not reach the solver project.",
                                 f"status.data == NO_DATA after upload "
                                 f"(status={response.get('status')!r})")
        self.diagnostics.upload_id = upload_id
        self.diagnostics.bytes_transferred += total_bytes

    def _await_build(self) -> None:
        deadline = time.monotonic() + self._build_timeout
        while True:
            self._check_cancel()
            response = self._status()
            status = str(response.get("status", ""))
            if status == STATUS_READY:
                return
            if status == STATUS_FAILED:
                raise self._fail_from_status(response, "building")
            if status == STATUS_BUILDING:
                progress = response.get("progress")
                info = str(response.get("info", "") or "Building solver project")
                activity_code, activity_message = self._runtime_activity()
                if activity_message:
                    info = activity_message
                if isinstance(progress, (int, float)):
                    self._event("BUILDING", info,
                                frame_current=int(progress * 100),
                                frame_total=100,
                                activity_code=activity_code)
                else:
                    self._event("BUILDING", info, indeterminate=True,
                                activity_code=activity_code)
            elif status in (STATUS_BUSY, STATUS_SAVE_AND_QUIT):
                raise _session_error(
                    "The solver project is unexpectedly busy.",
                    f"status {status} while waiting for the build")
            if time.monotonic() > deadline:
                raise _session_error("The solver build timed out.",
                                     f"no READY status within "
                                     f"{self._build_timeout}s")
            time.sleep(self._poll_interval)

    def _fetch_output_map(self) -> results.OutputMap:
        assert self._address is not None
        blob = wire.data_receive(self._address, self.transport,
                                 project_name=self.scene.project_name,
                                 path=results.MAP_PATH)
        schema_version = int(self.resolved.schema_version or "1")
        output_map = results.parse_output_map(
            blob, schema_version=schema_version)
        self.diagnostics.bytes_transferred += len(blob)
        targets = self.scene.dynamic_objects
        solid_targets = [target for target in targets
                         if target.deformable_type == "SOLID"]
        surface_blob = None
        if solid_targets:
            surface_blob = wire.data_receive(
                self._address, self.transport,
                project_name=self.scene.project_name,
                path=results.SURFACE_MAP_PATH)
            self.diagnostics.bytes_transferred += len(surface_blob)
        total_vertices = max(index for values in output_map.indices_by_uuid.values()
                             for index in values) + 1
        for target in targets:
            if target.deformable_type == "SOLID":
                raw_indices = output_map.indices_by_uuid.get(target.uuid)
                if raw_indices is None:
                    raise results.ResultValidationError(
                        f"solver output map has no entry for {target.uuid}")
                assert surface_blob is not None
                self._surface_maps_by_uuid[target.uuid] = results.parse_surface_map(
                    surface_blob, target.uuid, target.vertex_count,
                    schema_version=schema_version)
            else:
                raw_indices = output_map.indices_for(target.uuid,
                                                     target.vertex_count)
            self._indices_by_uuid[target.uuid] = results.object_index_array(
                raw_indices, total_vertices=total_vertices, uuid=target.uuid)
        return output_map

    def _fetch_frame(self, output_map: results.OutputMap,
                     frame: int) -> SolverFrame:
        assert self._address is not None
        step = time.monotonic()
        blob = wire.data_receive(self._address, self.transport,
                                 project_name=self.scene.project_name,
                                 path=results.frame_file_path(frame))
        self.diagnostics.timings["frame_transfer"] = (
            self.diagnostics.timings.get("frame_transfer", 0.0)
            + time.monotonic() - step)
        self.diagnostics.bytes_transferred += len(blob)
        step = time.monotonic()
        positions = results.decode_frame_payload_numpy(blob)
        self.diagnostics.timings["frame_decode"] = (
            self.diagnostics.timings.get("frame_decode", 0.0)
            + time.monotonic() - step)
        step = time.monotonic()
        positions_by_uuid = {}
        for target in self.scene.dynamic_objects:
            indices = self._indices_by_uuid[target.uuid]
            surface_map = self._surface_maps_by_uuid.get(target.uuid)
            if surface_map is None:
                extracted = results.extract_object_frame_numpy(
                    positions, indices, frame=frame, uuid=target.uuid,
                    expected_count=target.vertex_count)
                positions_by_uuid[target.uuid] = extracted
                continue
            tet_world = positions[indices]
            world = np.asarray(target.world_matrix,
                               dtype=np.float64)
            inverse = np.linalg.inv(world)
            homogeneous = np.concatenate(
                (tet_world.astype(np.float64),
                 np.ones((len(tet_world), 1))), axis=1)
            tet_local = (homogeneous @ inverse.T)[:, :3]
            smap = surface_map
            triangles = smap.surface_triangles[smap.tri_indices]
            v0, v1, v2 = (tet_local[triangles[:, index]] for index in range(3))
            b1, b2 = v1 - v0, v2 - v0
            normal = np.cross(b1, b2)
            length = np.linalg.norm(normal, axis=1)
            safe = length > 1e-10
            normal[safe] /= length[safe, None]
            normal[~safe] = 0.0
            c = smap.coefficients
            source_local = (v0 + c[:, 0:1] * b1 + c[:, 1:2] * b2
                            + c[:, 2:3] * normal)
            source_h = np.concatenate(
                (source_local, np.ones((len(source_local), 1))), axis=1)
            positions_by_uuid[target.uuid] = (
                source_h @ world.T)[:, :3].astype(np.float32)
        self.diagnostics.timings["frame_extract"] = (
            self.diagnostics.timings.get("frame_extract", 0.0)
            + time.monotonic() - step)
        first = positions_by_uuid[self.scene.dynamic_objects[0].uuid]
        return SolverFrame(frame, first, positions_by_uuid)

    def _simulate_and_fetch(self, *, resume: bool = False) -> None:
        total = self.scene.solver_frame_count
        if resume:
            assert self._address is not None
            response = wire.send_tcmd(
                self._address, self.transport, self.scene.project_name,
                wire.REQUEST_RESUME)
            self.diagnostics.note_status(str(response.get("status", "")))
        else:
            self._request(REQUEST_START_ALIAS)
        output_map: results.OutputMap | None = None
        fetched: set[int] = set(
            self._recovery.completed_solver_frames
            if self._recovery is not None and resume else ())
        deadline = time.monotonic() + self._simulate_timeout
        finished_status: str | None = None
        while len(fetched) < total:
            self._check_cancel()
            wait_step = time.monotonic()
            response = self._status()
            self.diagnostics.timings["simulation_wait"] = (
                self.diagnostics.timings.get("simulation_wait", 0.0)
                + time.monotonic() - wait_step)
            status = str(response.get("status", ""))
            solver_frame = response.get("frame")
            available = solver_frame if isinstance(solver_frame, int) else 0
            if status == STATUS_FAILED:
                raise self._fail_from_status(response, "simulating")
            if status in (STATUS_READY, STATUS_RESUMABLE):
                # Terminal without failure: everything produced is on disk.
                finished_status = status
                available = total
            if available > 0 and output_map is None:
                self._event("FETCHING", "Downloading solver output map",
                            indeterminate=True)
                output_map = self._fetch_output_map()
            for frame in range(1, min(available, total) + 1):
                if frame in fetched:
                    continue
                self._check_cancel()
                assert output_map is not None
                self._event("FETCHING",
                            f"Downloading frame {frame} of {total}",
                            frame_current=frame, frame_total=total)
                try:
                    solver_output = self._fetch_frame(output_map, frame)
                except ClothNextError as exc:
                    if finished_status is None:
                        raise
                    raise _session_error(
                        "The solver finished without producing every frame.",
                        "finished without producing every frame: "
                        f"status {finished_status}; frame {frame} could not be "
                        f"read after completion: {exc}") from exc
                self._frame_sink(solver_output)
                fetched.add(frame)
                self.diagnostics.fetched_frames.append(frame)
                deadline = time.monotonic() + self._simulate_timeout
            if len(fetched) >= total:
                break
            if finished_status is not None and len(fetched) < total:
                raise _session_error(
                    "The solver finished without producing every frame.",
                    f"status {finished_status} with only "
                    f"{sorted(fetched)} of {total} frames on disk")
            if status in (STATUS_BUSY, STATUS_SAVE_AND_QUIT, STATUS_BUILDING):
                current = min(available + 1, total)
                activity_code, activity_message = self._runtime_activity()
                self._event("SIMULATING",
                            activity_message or
                            f"Simulating frame {current} of {total}",
                            frame_current=available, frame_total=total,
                            activity_code=activity_code)
            else:
                self._event("SIMULATING",
                            f"Waiting for the solver ({status})",
                            indeterminate=True)
            if time.monotonic() > deadline:
                raise _session_error(
                    "The simulation stalled.",
                    f"no new frame within {self._simulate_timeout}s "
                    f"(status={status}, fetched={sorted(fetched)})")
            wait_step = time.monotonic()
            time.sleep(self._poll_interval)
            self.diagnostics.timings["simulation_wait"] = (
                self.diagnostics.timings.get("simulation_wait", 0.0)
                + time.monotonic() - wait_step)

    def _cancel_server_side(self) -> None:
        """State-aware cancellation: cancel_build during builds, terminate
        during simulation; then delete only our unique project."""
        if self._address is None:
            return
        try:
            response = wire.send_tcmd(self._address, self.transport,
                                      self.scene.project_name)
            status = str(response.get("status", ""))
            if status == STATUS_BUILDING:
                self._request(REQUEST_CANCEL_BUILD_ALIAS)
            elif status in (STATUS_BUSY, STATUS_SAVE_AND_QUIT):
                self._request(REQUEST_TERMINATE_ALIAS)
        except ClothNextError:
            pass  # the server may already be gone; process cleanup follows

    def _save_recovery_on_cancel(self) -> RecoveryOutcome:
        options, record = self._recovery, self._recovery_record
        if (options is None or record is None
                or not options.save_on_cancel or self._address is None):
            return RecoveryOutcome(
                checkpoint_saved=False,
                artist_message="Bake cancelled",
                technical_reason="",
                state_before="N/A",
                saved_states=(),
                kind=RecoveryOutcomeKind.NOT_ENABLED)

        verified_before = tuple(
            sorted(item.frame for item in self._recovery_record.checkpoints))
        # First, check current server status to decide if checkpoint is feasible
        try:
            initial_response = self._status()
            initial_status = str(initial_response.get("status", ""))
        except ClothNextError as exc:
            initial_status = "CONNECTION_ERROR"
            technical = f"initial status query failed: {exc.record.technical_message}"
            log_with_context(self._logger, logging.WARNING,
                "Recovery checkpoint: cannot query server status",
                {"project": self.scene.project_name,
                 "mode": self.diagnostics.solver_mode,
                 "status": initial_status,
                 "reason": technical,
                 "metadata_path": str(options.metadata_path)})
            self._event("RECOVERY_WARNING",
                        "Could not query solver for checkpoint status",
                        activity_code="RECOVERY_FAILED")
            if verified_before:
                current = self._recovery_record
                if current.state is not recovery.ProjectState.SAVED:
                    current = recovery.transition(
                        options.metadata_path, current,
                        recovery.ProjectState.SAVED, error=technical)
                self._recovery_record = recovery.transition(
                    options.metadata_path, current,
                    recovery.ProjectState.RESUMABLE, error=technical)
            return RecoveryOutcome(
                checkpoint_saved=bool(verified_before),
                artist_message=(
                    "Existing checkpoint preserved; newest state could not "
                    "be saved" if verified_before else
                    "Recovery checkpoint unavailable"),
                technical_reason=technical,
                state_before=initial_status,
                saved_states=verified_before,
                kind=(RecoveryOutcomeKind.EXISTING_PRESERVED
                      if verified_before else RecoveryOutcomeKind.FAILED))

        if (verified_before and initial_status in {
                STATUS_RESUMABLE, STATUS_READY, STATUS_FAILED}):
            log_with_context(
                self._logger, logging.INFO,
                "Existing recovery checkpoint preserved", {
                    "project": self.scene.project_name,
                    "status": initial_status,
                    "raw_server_frames": self._saved_states(initial_response),
                    "verified_metadata_frames": verified_before,
                })
            current = self._recovery_record
            if current.state is recovery.ProjectState.FAILED:
                self._recovery_record = recovery.transition(
                    options.metadata_path, current,
                    recovery.ProjectState.RESUMABLE)
            elif current.state is not recovery.ProjectState.RESUMABLE:
                if current.state is not recovery.ProjectState.SAVED:
                    current = recovery.transition(
                        options.metadata_path, current,
                        recovery.ProjectState.SAVED)
                self._recovery_record = recovery.transition(
                    options.metadata_path, current,
                    recovery.ProjectState.RESUMABLE)
            return RecoveryOutcome(
                checkpoint_saved=True,
                artist_message="Existing recovery checkpoint preserved",
                technical_reason="", state_before=initial_status,
                saved_states=verified_before,
                kind=RecoveryOutcomeKind.EXISTING_PRESERVED)

        # States where no simulation state exists yet — skip save_and_quit
        if initial_status in (STATUS_NO_DATA, STATUS_NO_BUILD, STATUS_BUILDING):
            log_with_context(self._logger, logging.INFO,
                "Recovery checkpoint skipped: no simulation state yet",
                {"project": self.scene.project_name,
                 "mode": self.diagnostics.solver_mode,
                 "status": initial_status,
                 "metadata_path": str(options.metadata_path)})
            self._event("RECOVERY_WARNING",
                        "No recovery checkpoint available yet",
                        activity_code="RECOVERY_TOO_EARLY")
            if verified_before:
                current = self._recovery_record
                if current.state is not recovery.ProjectState.SAVED:
                    current = recovery.transition(
                        options.metadata_path, current,
                        recovery.ProjectState.SAVED,
                        error=(f"server status {initial_status} has no new "
                               "simulation state to save"))
                self._recovery_record = recovery.transition(
                    options.metadata_path, current,
                    recovery.ProjectState.RESUMABLE,
                    error=(f"server status {initial_status} has no new "
                           "simulation state to save"))
                return RecoveryOutcome(
                    checkpoint_saved=True,
                    artist_message=(
                        "Existing checkpoint preserved; newest state "
                        "could not be saved"),
                    technical_reason=(
                        f"server status {initial_status} has no new "
                        "simulation state to save"),
                    state_before=initial_status,
                    saved_states=verified_before,
                    kind=RecoveryOutcomeKind.EXISTING_PRESERVED)
            return RecoveryOutcome(
                checkpoint_saved=False,
                artist_message="Bake cancelled before a recovery checkpoint was available",
                technical_reason="",
                state_before=initial_status,
                saved_states=(),
                kind=RecoveryOutcomeKind.NOT_AVAILABLE_YET)

        # States where we can attempt save_and_quit
        if initial_status not in (STATUS_BUSY, STATUS_SAVE_AND_QUIT, STATUS_READY, STATUS_RESUMABLE, STATUS_FAILED):
            log_with_context(self._logger, logging.WARNING,
                "Recovery checkpoint: unexpected server status, attempting save",
                {"project": self.scene.project_name,
                 "mode": self.diagnostics.solver_mode,
                 "status": initial_status,
                 "metadata_path": str(options.metadata_path)})
            # Don't return; proceed to attempt

        # Try to request save_and_quit and wait for confirmation
        try:
            record = recovery.transition(
                options.metadata_path, record,
                recovery.ProjectState.CHECKPOINT_REQUESTED)
            self._recovery_record = record
            if initial_status != STATUS_SAVE_AND_QUIT:
                self._request(wire.REQUEST_SAVE_AND_QUIT)
            deadline = time.monotonic() + min(self._simulate_timeout, 120.0)
            last_known_status = initial_status
            while time.monotonic() < deadline:
                response = self._status()
                status = str(response.get("status", ""))
                last_known_status = status
                saved = self._saved_states(response)
                if saved:
                    self._sync_checkpoints(response)
                verified = tuple(sorted(
                    item.frame for item in self._recovery_record.checkpoints))
                log_with_context(
                    self._logger, logging.DEBUG,
                    "Recovery checkpoint verification poll", {
                        "project": self.scene.project_name,
                        "status": status,
                        "raw_server_frames": saved,
                        "verified_metadata_frames": verified,
                    })
                newly_verified = tuple(
                    frame for frame in verified
                    if frame not in verified_before)
                if (status in (STATUS_RESUMABLE, STATUS_READY, STATUS_FAILED)
                        and (newly_verified or not verified_before)
                        and verified):
                    record = self._recovery_record
                    assert record is not None
                    record = recovery.transition(
                        options.metadata_path, record,
                        recovery.ProjectState.SAVED)
                    self._recovery_record = recovery.transition(
                        options.metadata_path, record,
                        recovery.ProjectState.RESUMABLE)
                    outcome = RecoveryOutcome(
                        checkpoint_saved=True,
                        artist_message="Recovery checkpoint saved",
                        technical_reason="",
                        state_before=initial_status,
                        saved_states=verified,
                        kind=RecoveryOutcomeKind.SAVED)
                    self._event("RECOVERY_SAVED", outcome.artist_message,
                                activity_code="RECOVERY_SAVED")
                    return outcome
                time.sleep(self._poll_interval)

            # Timeout waiting for confirmation
            verified = tuple(sorted(
                item.frame for item in self._recovery_record.checkpoints))
            technical = (f"timed out after {min(self._simulate_timeout, 120.0)}s "
                         f"waiting for a verified checkpoint; "
                         f"last status={last_known_status}; "
                         f"raw server frames={tuple(self._known_saved_states)}; "
                         f"verified metadata frames={verified}")
            log_with_context(self._logger, logging.WARNING,
                "Recovery checkpoint timed out",
                {"project": self.scene.project_name,
                 "mode": self.diagnostics.solver_mode,
                 "status": last_known_status,
                 "raw_server_frames": tuple(self._known_saved_states),
                 "verified_metadata_frames": verified,
                 "timeout_s": min(self._simulate_timeout, 120.0),
                 "metadata_path": str(options.metadata_path)})
            self._event("RECOVERY_WARNING",
                        "Recovery checkpoint could not be confirmed",
                        activity_code="RECOVERY_TIMEOUT")
            if verified:
                record = self._recovery_record
                assert record is not None
                record = recovery.transition(
                    options.metadata_path, record,
                    recovery.ProjectState.SAVED, error=technical)
                self._recovery_record = recovery.transition(
                    options.metadata_path, record,
                    recovery.ProjectState.RESUMABLE, error=technical)
                return RecoveryOutcome(
                    checkpoint_saved=True,
                    artist_message=(
                        "Existing checkpoint preserved; newest state "
                        "could not be saved"),
                    technical_reason=technical,
                    state_before=initial_status,
                    saved_states=verified,
                    timed_out=True,
                    kind=RecoveryOutcomeKind.EXISTING_PRESERVED)
            try:
                self._recovery_record = recovery.transition(
                    options.metadata_path, self._recovery_record,
                    recovery.ProjectState.FAILED,
                    error=technical)
            except (OSError, ValueError) as exc:
                log_with_context(self._logger, logging.WARNING,
                    "Failed to record recovery timeout in metadata",
                    {"project": self.scene.project_name,
                     "error": str(exc)})
            return RecoveryOutcome(
                checkpoint_saved=False,
                artist_message="Recovery checkpoint could not be saved",
                technical_reason=technical,
                state_before=initial_status,
                saved_states=verified,
                timed_out=True,
                kind=RecoveryOutcomeKind.FAILED)

        except (ClothNextError, OSError, ValueError) as exc:
            # Preserve the actual exception for logging
            technical = f"{type(exc).__name__}: {exc}"
            log_with_context(self._logger, logging.WARNING,
                "Recovery checkpoint failed",
                {"project": self.scene.project_name,
                 "mode": self.diagnostics.solver_mode,
                 "status": initial_status,
                 "exception_type": type(exc).__name__,
                 "exception_message": str(exc),
                 "metadata_path": str(options.metadata_path)})
            self._event("RECOVERY_WARNING",
                        "Recovery checkpoint could not be saved",
                        activity_code="RECOVERY_FAILED")
            verified = tuple(sorted(
                item.frame for item in self._recovery_record.checkpoints
            )) if self._recovery_record is not None else ()
            if self._recovery_record is not None:
                try:
                    self._recovery_record = recovery.transition(
                        options.metadata_path, self._recovery_record,
                        recovery.ProjectState.FAILED,
                        error=f"save_and_quit failed: {technical}")
                except (OSError, ValueError) as meta_exc:
                    log_with_context(self._logger, logging.WARNING,
                        "Failed to record recovery failure in metadata",
                        {"project": self.scene.project_name,
                         "original_error": technical,
                         "meta_error": str(meta_exc)})
            return RecoveryOutcome(
                checkpoint_saved=bool(verified),
                artist_message=(
                    "Existing checkpoint preserved; newest state could not "
                    "be saved" if verified else
                    "Recovery checkpoint could not be saved"),
                technical_reason=technical,
                state_before=initial_status,
                saved_states=verified,
                kind=(RecoveryOutcomeKind.EXISTING_PRESERVED if verified
                      else RecoveryOutcomeKind.FAILED))

    def _delete_project(self) -> None:
        if self._address is None:
            return
        try:
            self._request(REQUEST_DELETE_ALIAS)
        except ClothNextError:
            pass

    def _stop_owned(self) -> None:
        if self._manager is not None:
            self._capture_process_tails()
            try:
                poll = self._manager.stop()
                self.diagnostics.stdout_tail = poll.stdout_tail
                self.diagnostics.stderr_tail = poll.stderr_tail
                self.diagnostics.contact_peak = poll.contact_peak
                self.diagnostics.contact_last = poll.contact_last
                self.diagnostics.contact_samples = poll.contact_samples
            finally:
                self._manager = None

    # -- entry point ---------------------------------------------------------

    def run(self) -> SessionDiagnostics:
        """Execute the full vertical slice; raises on failure, returns
        diagnostics on success. Cleanup always runs."""
        started = time.monotonic()
        owned = self.resolved.ownership is ConnectionOwnership.OWNED_PROCESS
        completed = False
        preserved = False
        try:
            self._check_cancel()
            self._recovery_start()
            if owned:
                self._event("STARTING_SOLVER", "Starting PPF solver",
                            indeterminate=True)
                step = time.monotonic()
                self._start_owned_solver()
                self._metadata_event()
                self.diagnostics.timings["start_solver"] = time.monotonic() - step
            else:
                assert self._address is not None
                self.diagnostics.host = self._address.host
                self.diagnostics.port = self._address.port
                self._event("STARTING_SOLVER", "Connecting to the PPF server",
                            indeterminate=True)
                self._status()
                self._metadata_event()
            self._check_cancel()
            resuming = bool(self._recovery and self._recovery.resume)
            if resuming:
                response = self._status()
                if (str(response.get("data_hash", "")) != self.scene.data_hash
                        or str(response.get("param_hash", ""))
                        != self.scene.param_hash):
                    raise _session_error(
                        "The saved solver project does not match this Bake.",
                        "server data/param hash differs from recovery identity")
                if not self._saved_states(response):
                    raise _session_error(
                        "The recovery project has no confirmed Saved State.",
                        "server status has no saved_states")
            else:
                self._event("UPLOADING", "Uploading scene",
                            indeterminate=True)
                step = time.monotonic()
                self._upload()
                self.diagnostics.timings["upload"] = (
                    time.monotonic() - step)
                self._check_cancel()
                self._event("BUILDING", "Building solver project",
                            indeterminate=True)
                step = time.monotonic()
                self._request(REQUEST_BUILD_ALIAS)
                self._await_build()
                self.diagnostics.timings["build"] = (
                    time.monotonic() - step)
            self._check_cancel()
            step = time.monotonic()
            self._simulate_and_fetch(resume=resuming)
            self.diagnostics.timings["simulation_and_import"] = time.monotonic() - step
            completed = True
            if self._recovery_record is not None and self._recovery is not None:
                self._recovery_record = recovery.transition(
                    self._recovery.metadata_path, self._recovery_record,
                    recovery.ProjectState.FINISHED,
                    last_frame=self.scene.solver_frame_count)
                preserved = self._recovery.keep_on_finish
            return self.diagnostics
        except SessionCancelled:
            self.diagnostics.cancelled = True
            self._event("CANCELLING", "Saving recovery checkpoint",
                        indeterminate=True)
            outcome = self._save_recovery_on_cancel()
            if not outcome.checkpoint_saved:
                self._cancel_server_side()
            raise SessionCancelled(resumable=outcome.checkpoint_saved,
                                   recovery_outcome=outcome)
        except ClothNextError as exc:
            if self._recovery_record is not None and self._recovery is not None:
                try:
                    state = (recovery.ProjectState.RESUMABLE
                             if self._recovery_record.checkpoints
                             else recovery.ProjectState.FAILED)
                    self._recovery_record = recovery.transition(
                        self._recovery.metadata_path, self._recovery_record,
                        state, error=exc.record.technical_message)
                    preserved = bool(self._recovery_record.checkpoints)
                except (OSError, ValueError):
                    pass
            if owned and exc.record.category is ErrorCategory.SOLVER_CONNECTION:
                raise self._owned_connection_error(exc) from exc
            raise
        finally:
            try:
                if (self._recovery is None or completed) and not preserved:
                    self._delete_project()
                    if (self._recovery_record is not None
                            and self._recovery is not None):
                        try:
                            self._recovery_record = recovery.transition(
                                self._recovery.metadata_path,
                                self._recovery_record,
                                recovery.ProjectState.DELETED)
                        except (OSError, ValueError):
                            pass
            finally:
                if owned:
                    self._stop_owned()
                payload = self.scene.data_payload
                if isinstance(payload, Path):
                    try:
                        if payload.parent.resolve() == self.work_directory.resolve():
                            payload.unlink(missing_ok=True)
                    except OSError:
                        pass
                self.diagnostics.timings["total"] = time.monotonic() - started
                log_with_context(self._logger, 20, "session finished", {
                    "run_id": self.diagnostics.run_id,
                    "project": self.scene.project_name,
                    "mode": self.diagnostics.solver_mode,
                    "fetched": len(self.diagnostics.fetched_frames),
                    "cancelled": self.diagnostics.cancelled,
                    "contact_peak": self.diagnostics.contact_peak,
                    "contact_last": self.diagnostics.contact_last,
                    "contact_samples": self.diagnostics.contact_samples,
                })


# Aliases keep the request spellings in one importable place for tests.
REQUEST_BUILD_ALIAS = wire.REQUEST_BUILD
REQUEST_CANCEL_BUILD_ALIAS = wire.REQUEST_CANCEL_BUILD
REQUEST_START_ALIAS = wire.REQUEST_START
REQUEST_TERMINATE_ALIAS = wire.REQUEST_TERMINATE
REQUEST_DELETE_ALIAS = wire.REQUEST_DELETE
