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

import threading
import time
import uuid as uuid_module
from dataclasses import dataclass, field
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


class SessionCancelled(Exception):
    """The run was cancelled cooperatively; not an error."""


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
    data_payload: bytes
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
    stdout_tail: tuple[str, ...] = ()
    stderr_tail: tuple[str, ...] = ()
    contact_peak: int = 0
    contact_last: int = 0
    contact_samples: int = 0
    cancelled: bool = False
    bytes_transferred: int = 0

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
                 simulate_timeout: float = 600.0) -> None:
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
        self._manager: SolverProcessManager | None = None
        self._address: wire.ServerAddress | None = external_address
        self._logger = get_logger("solver.session")
        self._indices_by_uuid: dict[str, np.ndarray] = {}
        self._surface_maps_by_uuid: dict[str, results.SurfaceMap] = {}
        self.diagnostics = SessionDiagnostics(project_name=scene.project_name,
                                              solver_mode=resolved.mode.name,
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
        return response

    def _runtime_activity(self) -> tuple[str, str]:
        if self._manager is None:
            return "", ""
        poll = self._manager.poll()
        return poll.activity_code, poll.activity_message

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
        return _session_error(
            f"The solver reported a failure while {phase}.",
            f"server status FAILED during {phase}: {error_text}; "
            f"contacts(last={self.diagnostics.contact_last}, "
            f"peak={self.diagnostics.contact_peak}, "
            f"samples={self.diagnostics.contact_samples}); "
            f"stdout_tail={self.diagnostics.stdout_tail}; "
            f"stderr_tail={self.diagnostics.stderr_tail}")

    # -- lifecycle ----------------------------------------------------------

    def _start_owned_solver(self) -> None:
        executable = self.resolved.executable_path
        assert executable is not None
        root = bundle_root_for(executable)
        layout = BundledSolverLayout.from_root(root)
        server_data = self.work_directory / "server-data"
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
        output_map = results.parse_output_map(blob)
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
                    surface_blob, target.uuid, target.vertex_count)
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

    def _simulate_and_fetch(self) -> None:
        total = self.scene.solver_frame_count
        self._request(REQUEST_START_ALIAS)
        output_map: results.OutputMap | None = None
        fetched: set[int] = set()
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
        try:
            self._check_cancel()
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
            self._event("UPLOADING", "Uploading scene", indeterminate=True)
            step = time.monotonic()
            self._upload()
            self.diagnostics.timings["upload"] = time.monotonic() - step
            self._check_cancel()
            self._event("BUILDING", "Building solver project",
                        indeterminate=True)
            step = time.monotonic()
            self._request(REQUEST_BUILD_ALIAS)
            self._await_build()
            self.diagnostics.timings["build"] = time.monotonic() - step
            self._check_cancel()
            step = time.monotonic()
            self._simulate_and_fetch()
            self.diagnostics.timings["simulation_and_import"] = time.monotonic() - step
            return self.diagnostics
        except SessionCancelled:
            self.diagnostics.cancelled = True
            self._event("CANCELLING", "Cancelling solver run",
                        indeterminate=True)
            self._cancel_server_side()
            raise
        except ClothNextError as exc:
            if owned and exc.record.category is ErrorCategory.SOLVER_CONNECTION:
                raise self._owned_connection_error(exc) from exc
            raise
        finally:
            try:
                self._delete_project()
            finally:
                if owned:
                    self._stop_owned()
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
