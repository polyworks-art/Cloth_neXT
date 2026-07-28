# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure session-service logic against a scripted wire layer (no sockets,
no solver, no bpy): lifecycle order, cancellation decisions, ownership
safety, result validation, and playback conversion."""

from __future__ import annotations

import re
import gzip
import json
import struct
import threading
from pathlib import Path

import pytest

from cloth_next.core.errors import ClothNextError, ErrorCategory, ErrorRecord
from cloth_next import recovery
from cloth_next.ppf import wire
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.resolver import ResolvedSolver, SolverMode
from cloth_next.ppf.schema import cbor_codec
from cloth_next.ppf_run import import_result, session as session_module
from cloth_next.ppf_run.session import (RecoveryOptions, RecoveryOutcome,
                                        RecoveryOutcomeKind,
                                        SessionCancelled, SessionScene,
                                        SessionDeformable, SolverFrame, SolverSession,
                                        new_project_name)


def _scene(frame_count=8) -> SessionScene:
    return SessionScene(
        project_name="clothnext_test0001",
        cloth_name="Cloth", cloth_uuid="uuid-cloth", cloth_vertex_count=4,
        collider_name="Collider", collider_uuid="uuid-collider",
        frame_count=frame_count,
        data_payload=b"data", param_payload=b"param",
        data_hash="dh", param_hash="ph")


def _external_resolved() -> ResolvedSolver:
    return ResolvedSolver(SolverMode.EXTERNAL_SERVER, None, None, None, None,
                          None, ConnectionOwnership.EXTERNAL_SERVER, None,
                          False)


def _frame_blob(count=8, offset=0.0) -> bytes:
    values = []
    for i in range(count):
        values += [float(i), offset, 0.0]
    return struct.pack(f"<{len(values)}f", *values)


def _vertex_map_blob() -> bytes:
    return cbor_codec.dumps({
        "version": 1, "kind": "VertexMap",
        "payload": {"uuid-cloth": [0, 1, 2, 3],
                    "uuid-collider": [4, 5, 6, 7]}})


class ScriptedWire:
    """Replaces the wire module functions with a scripted server."""

    def __init__(self, monkeypatch, *, frames_per_poll=2):
        self.log: list[tuple] = []
        self.status_index = 0
        self.frames_per_poll = frames_per_poll
        self.solver_frames = 7
        self.fail_after_upload = False
        self.hang_in_build = False
        self.hang_in_sim = False
        base = {"upload_id": "u123", "data_hash": "dh", "param_hash": "ph",
                "error": "", "data": "READY", "initialized": True}
        self.base = base
        self.sim_polls = 0
        monkeypatch.setattr(wire, "send_tcmd", self._send_tcmd)
        monkeypatch.setattr(wire, "upload_atomic", self._upload_atomic)
        monkeypatch.setattr(wire, "data_receive", self._data_receive)
        # session imported the names at module level? No: it calls wire.<fn>.

    def _send_tcmd(self, _address, _config, project, request=None, *,
                   frame=None):
        self.log.append(("tcmd", project, request))
        if request == "build":
            return {**self.base, "status": "BUILDING"}
        if request == "start":
            return {**self.base, "status": "BUSY", "frame": 0}
        if request in ("cancel_build", "terminate", "delete"):
            return {**self.base, "status": "NO_DATA"}
        # status poll
        if self.hang_in_build:
            return {**self.base, "status": "BUILDING", "progress": 0.5,
                    "info": "building"}
        if not any(entry[2] == "build" for entry in self.log):
            return {**self.base, "status": "NO_BUILD", "frame": 0}
        if not any(entry[2] == "start" for entry in self.log):
            if self.fail_after_upload:
                return {**self.base, "status": "FAILED",
                        "error": "decode exploded"}
            return {**self.base, "status": "READY", "frame": 0}
        if self.hang_in_sim:
            return {**self.base, "status": "BUSY", "frame": 0}
        self.sim_polls += 1
        frame = min(self.sim_polls * self.frames_per_poll, self.solver_frames)
        status = "READY" if frame >= self.solver_frames else "BUSY"
        return {**self.base, "status": status, "frame": frame,
                "total_frames": self.solver_frames}

    def _upload_atomic(self, _address, _config, *, project_name, data_payload,
                       param_payload, data_hash, param_hash):
        self.log.append(("upload", project_name, len(data_payload),
                         len(param_payload), data_hash, param_hash))

    def _data_receive(self, _address, _config, *, project_name, path,
                      max_bytes=0):
        self.log.append(("receive", project_name, path))
        if path == "session/map.pickle":
            return _vertex_map_blob()
        match = re.fullmatch(r"session/output/vert_(\d+)\.bin", path)
        assert match, path
        return _frame_blob(count=8, offset=float(match[1]))


def _run_session(monkeypatch, scripted=None, **kwargs):
    scripted = scripted or ScriptedWire(monkeypatch)
    frames: list[SolverFrame] = []
    events: list = []
    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=Path("."),
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        emit=events.append, frame_sink=frames.append, poll_interval=0.001,
        **kwargs)
    return session, scripted, frames, events


def test_full_lifecycle_order_and_frames(monkeypatch):
    session, scripted, frames, events = _run_session(monkeypatch)
    diagnostics = session.run()
    requests = [entry[2] for entry in scripted.log if entry[0] == "tcmd"
                and entry[2] is not None]
    assert requests[0] == "build"
    assert requests[1] == "start"
    assert requests[-1] == "delete"
    assert "terminate" not in requests and "cancel_build" not in requests
    uploads = [entry for entry in scripted.log if entry[0] == "upload"]
    assert uploads == [("upload", "clothnext_test0001", 4, 5, "dh", "ph")]
    assert [f.solver_frame for f in frames] == [1, 2, 3, 4, 5, 6, 7]
    assert all(len(f.positions_solver_world) == 4 for f in frames)
    assert diagnostics.upload_id == "u123"
    assert diagnostics.fetched_frames == [1, 2, 3, 4, 5, 6, 7]
    phases = [event.phase for event in events]
    assert phases[0] == "STARTING_SOLVER"
    assert "UPLOADING" in phases and "BUILDING" in phases
    assert "FETCHING" in phases


def test_frames_are_split_for_multiple_deformables(monkeypatch):
    class MultiWire(ScriptedWire):
        def _data_receive(self, address, config, *, project_name, path,
                          max_bytes=0):
            self.log.append(("receive", project_name, path))
            if path == "session/map.pickle":
                return cbor_codec.dumps({
                    "version": 1, "kind": "VertexMap",
                    "payload": {"uuid-cloth": [0, 1, 2, 3],
                                "uuid-rod": [4, 5],
                                "uuid-collider": [6, 7, 8, 9]}})
            match = re.fullmatch(r"session/output/vert_(\d+)\.bin", path)
            assert match, path
            return _frame_blob(count=10, offset=float(match[1]))

    scripted = MultiWire(monkeypatch)
    scene = _scene()
    scene = SessionScene(
        scene.project_name, scene.cloth_name, scene.cloth_uuid,
        scene.cloth_vertex_count, scene.collider_name, scene.collider_uuid,
        scene.frame_count, scene.data_payload, scene.param_payload,
        scene.data_hash, scene.param_hash,
        deformables=(
            SessionDeformable("Cloth", "uuid-cloth", 4, "SHELL"),
            SessionDeformable("Cable", "uuid-rod", 2, "ROD")))
    frames = []
    session = SolverSession(
        resolved=_external_resolved(), scene=scene, work_directory=Path("."),
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        frame_sink=frames.append, poll_interval=0.001)
    session.run()
    assert len(frames) == 7
    assert set(frames[0].positions_by_uuid) == {"uuid-cloth", "uuid-rod"}
    assert frames[0].positions_by_uuid["uuid-cloth"].shape == (4, 3)
    assert frames[0].positions_by_uuid["uuid-rod"].shape == (2, 3)
    assert frames[0].positions_solver_world is frames[0].positions_by_uuid["uuid-cloth"]


def test_upload_hash_mismatch_aborts(monkeypatch):
    scripted = ScriptedWire(monkeypatch)
    scripted.base["param_hash"] = "WRONG"
    session, _s, _f, _e = _run_session(monkeypatch, scripted)
    with pytest.raises(ClothNextError, match="hash mismatch"):
        session.run()
    requests = [entry[2] for entry in scripted.log if entry[0] == "tcmd"]
    assert "build" not in requests
    assert "delete" in requests  # cleanup still ran


def test_build_failure_surfaces_server_error(monkeypatch):
    scripted = ScriptedWire(monkeypatch)
    scripted.fail_after_upload = True
    session, _s, _f, _e = _run_session(monkeypatch, scripted)
    with pytest.raises(ClothNextError, match="building"):
        session.run()


def test_cancel_during_build_sends_cancel_build_then_delete(monkeypatch):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_build = True
    cancel = threading.Event()
    frames: list = []
    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(), work_directory=Path("."),
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        cancel_event=cancel, frame_sink=frames.append, poll_interval=0.001,
        emit=lambda event: cancel.set())  # cancel on the first BUILDING event
    with pytest.raises(SessionCancelled):
        session.run()
    requests = [entry[2] for entry in scripted.log if entry[0] == "tcmd"
                and entry[2] is not None]
    assert "cancel_build" in requests
    assert "terminate" not in requests
    assert requests[-1] == "delete"
    assert session.diagnostics.cancelled


def test_cancel_during_simulation_sends_terminate(monkeypatch):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_sim = True
    cancel = threading.Event()
    events: list = []

    def emit(event):
        events.append(event)
        if event.phase == "SIMULATING":
            cancel.set()

    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(), work_directory=Path("."),
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        cancel_event=cancel, poll_interval=0.001, emit=emit)
    with pytest.raises(SessionCancelled):
        session.run()
    requests = [entry[2] for entry in scripted.log if entry[0] == "tcmd"
                and entry[2] is not None]
    assert "terminate" in requests
    assert "cancel_build" not in requests
    assert requests[-1] == "delete"


def test_finished_without_all_frames_is_an_error(monkeypatch):
    scripted = ScriptedWire(monkeypatch)

    original = scripted._send_tcmd

    def early_finish(_address, _config, project, request=None):
        response = original(_address, _config, project, request)
        if request is None and response.get("status") in ("BUSY", "READY") \
                and any(e[2] == "start" for e in scripted.log):
            return {**scripted.base, "status": "READY", "frame": 3}
        return response

    monkeypatch.setattr(wire, "send_tcmd", early_finish)
    original_receive = scripted._data_receive

    def missing_late_frames(*args, **kwargs):
        path = kwargs["path"]
        match = re.fullmatch(r"session/output/vert_(\d+)\.bin", path)
        if match and int(match.group(1)) > 3:
            raise ClothNextError(session_module.ErrorRecord.create(
                category=session_module.ErrorCategory.SIMULATION,
                user_message="Missing output frame.",
                technical_message="server error during data_receive: File not found",
                recommended_action="Retry."))
        return original_receive(*args, **kwargs)

    monkeypatch.setattr(wire, "data_receive", missing_late_frames)
    session, _s, _f, _e = _run_session(monkeypatch, scripted)
    with pytest.raises(ClothNextError, match="without producing every frame"):
        session.run()

def test_runtime_metadata_event_is_immutable_and_safe(monkeypatch):
    scripted=ScriptedWire(monkeypatch)
    session,_scripted,_frames,events=_run_session(monkeypatch,scripted)
    session.run()
    metadata=[event for event in events if event.phase=="RUNTIME_METADATA"]
    assert len(metadata)==1
    assert metadata[0].solver_mode=="EXTERNAL_SERVER"
    assert metadata[0].process_id is None


def test_external_server_is_never_stopped(monkeypatch):
    session, scripted, frames, _events = _run_session(monkeypatch)
    assert session._manager is None
    session.run()
    assert session._manager is None  # no owned process was ever created
    # ownership rule: the resolver marked this EXTERNAL_SERVER
    assert session.resolved.ownership is ConnectionOwnership.EXTERNAL_SERVER


def test_project_name_generation():
    names = {new_project_name() for _ in range(64)}
    assert len(names) == 64
    for name in names:
        assert re.fullmatch(r"clothnext_[0-9a-f]{12}", name)


def test_owned_solver_fails_before_start_when_native_worker_was_quarantined(
        tmp_path):
    executable = tmp_path / "ppf-cts-server.exe"
    executable.write_bytes(b"server")
    resolved = ResolvedSolver(
        SolverMode.DEVELOPMENT, tmp_path, executable, "0.1.0", "0.11", "1",
        ConnectionOwnership.OWNED_PROCESS, None, True)
    session = SolverSession(
        resolved=resolved, scene=_scene(), work_directory=tmp_path / "work")

    with pytest.raises(ClothNextError, match="worker is missing") as caught:
        session._start_owned_solver()

    assert caught.value.record.category is session_module.ErrorCategory.SOLVER_INSTALLATION
    assert "Security software" in caught.value.record.user_message
    assert "allow its installation folder" in \
        caught.value.record.recommended_action


def _recovery_identity():
    return recovery.RecoveryIdentity(
        scene_key="scene", param_key="param",
        export_uuids=("uuid-cloth", "uuid-collider"),
        geometry_fingerprint="geometry",
        topology_fingerprint="topology", frame_start=1, frame_end=8,
        fps=24.0, collider_sampling=(("uuid-collider", 8),),
        solver_version="0.1.0", protocol_version="0.11",
        solver_schema_version="1")


def _session_with_violation_sidecar(tmp_path):
    scene = _scene()
    server_root = tmp_path / "server"
    project_root = server_root / scene.project_name
    project_root.mkdir(parents=True)
    options = RecoveryOptions(
        enabled=True,
        metadata_path=tmp_path / "recovery" / "metadata.json",
        identity=_recovery_identity(),
        server_data_root=server_root)
    session = SolverSession(
        resolved=_external_resolved(), scene=scene,
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9),
        recovery_options=options)
    return session, project_root / "build_violations.json"


def test_failed_status_loads_structured_violations_from_build_sidecar(
        tmp_path):
    session, sidecar = _session_with_violation_sidecar(tmp_path)
    expected = {
        "type": "self_intersection",
        "combined_pair": [3, 9],
        "elements": [
            {"kind": "TRIANGLE", "combined_triangle_index": 3},
            {"kind": "TRIANGLE", "combined_triangle_index": 9},
        ]}
    sidecar.write_text(
        json.dumps({"violations": [expected]}), encoding="utf-8")

    error = session._fail_from_status(
        {"status": "FAILED", "error": "Intersections detected (1)."},
        "building")

    assert error.violations == (expected,)


def test_failed_status_loads_build_sidecar_when_recovery_is_disabled(
        tmp_path):
    scene = _scene()
    work_directory = tmp_path / "run"
    sidecar = (
        work_directory / "server-data" / scene.project_name
        / "build_violations.json")
    sidecar.parent.mkdir(parents=True)
    expected = {
        "type": "self_intersection",
        "tris": [[[0.0, 0.0, 0.0],
                  [1.0, 0.0, 0.0],
                  [0.0, 1.0, 0.0]]]}
    sidecar.write_text(
        json.dumps({"violations": [expected]}), encoding="utf-8")
    session = SolverSession(
        resolved=_external_resolved(), scene=scene,
        work_directory=work_directory,
        external_address=wire.ServerAddress("127.0.0.1", 9),
        recovery_options=None)

    error = session._fail_from_status(
        {"status": "FAILED", "error": "Intersections detected (1)."},
        "building")

    assert error.violations == (expected,)


def test_failed_status_loads_mirrored_build_sidecar(tmp_path):
    scene = _scene()
    work_directory = tmp_path / "run"
    server_root = work_directory / "server-data"
    server_root.mkdir(parents=True)
    expected = {"type": "self_intersection", "combined_pair": [4, 8]}
    (server_root / f"{scene.project_name}.build_violations.json").write_text(
        json.dumps({"violations": [expected]}), encoding="utf-8")
    session = SolverSession(
        resolved=_external_resolved(), scene=scene,
        work_directory=work_directory,
        external_address=wire.ServerAddress("127.0.0.1", 9))

    error = session._fail_from_status(
        {"status": "FAILED", "error": "Intersections detected (1)."},
        "building")

    assert error.violations == (expected,)


def test_build_sidecar_confirmation_waits_for_atomic_mirror(
        monkeypatch, tmp_path):
    scene = _scene()
    work_directory = tmp_path / "run"
    server_root = work_directory / "server-data"
    server_root.mkdir(parents=True)
    mirror = server_root / f"{scene.project_name}.build_violations.json"
    expected = {"type": "self_intersection", "combined_pair": [12, 14]}
    session = SolverSession(
        resolved=_external_resolved(), scene=scene,
        work_directory=work_directory,
        external_address=wire.ServerAddress("127.0.0.1", 9))
    sleeps = 0

    def publish_on_first_wait(_seconds):
        nonlocal sleeps
        sleeps += 1
        mirror.write_text(
            json.dumps({"violations": [expected]}), encoding="utf-8")

    monkeypatch.setattr(session_module.time, "sleep", publish_on_first_wait)

    error = session._fail_from_status(
        {"status": "FAILED", "error": "Intersections detected (1)."},
        "building")

    assert sleeps == 1
    assert error.violations == (expected,)


def test_status_violations_take_precedence_over_stale_sidecar(tmp_path):
    session, sidecar = _session_with_violation_sidecar(tmp_path)
    sidecar.write_text(
        json.dumps({"violations": [{"combined_pair": [1, 2]}]}),
        encoding="utf-8")
    current = {"combined_pair": [7, 8]}

    error = session._fail_from_status({
        "status": "FAILED", "error": "failed",
        "violations": [json.dumps(current)]}, "building")

    assert error.violations == (current,)


@pytest.mark.parametrize("payload", ("", "{broken", "[]",
                                     '{"violations": "invalid"}'))
def test_invalid_build_violation_sidecar_is_not_presented(tmp_path, payload):
    session, sidecar = _session_with_violation_sidecar(tmp_path)
    sidecar.write_text(payload, encoding="utf-8")

    error = session._fail_from_status(
        {"status": "FAILED", "error": "failed"}, "building")

    assert error.violations == ()


def test_controlled_cancel_confirms_saved_state_and_preserves_project(
        monkeypatch, tmp_path):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_sim = True
    server_root = tmp_path / "server"
    (server_root / _scene().project_name).mkdir(parents=True)
    project_root = server_root / _scene().project_name
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    saved = False
    original = scripted._send_tcmd

    def recovery_wire(address, config, project, request=None, *, frame=None):
        nonlocal saved
        if request == "save_and_quit":
            scripted.log.append(("tcmd", project, request))
            (output / "state_2.bin.gz").write_bytes(
                gzip.compress(b"confirmed-state"))
            saved = True
            return {**scripted.base, "status": "SAVE_AND_QUIT",
                    "frame": 2, "saved_states": []}
        response = original(address, config, project, request, frame=frame)
        if saved and request is None:
            return {**scripted.base, "status": "RESUMABLE",
                    "frame": 2, "saved_states": [2]}
        return response

    monkeypatch.setattr(wire, "send_tcmd", recovery_wire)
    cancel = threading.Event()

    def emit(event):
        if event.phase == "SIMULATING":
            cancel.set()

    metadata = tmp_path / "recovery" / "metadata.json"
    options = RecoveryOptions(
        True, metadata, _recovery_identity(), server_root,
        save_on_cancel=True)
    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        cancel_event=cancel, emit=emit, poll_interval=0.001,
        recovery_options=options)
    with pytest.raises(SessionCancelled) as raised:
        session.run()
    assert raised.value.resumable
    outcome = raised.value.recovery_outcome
    assert outcome is not None
    assert outcome.checkpoint_saved
    assert outcome.kind is RecoveryOutcomeKind.SAVED
    assert outcome.saved_states == (2,)
    assert outcome.artist_message
    assert outcome.state_before in ("BUSY", "SAVE_AND_QUIT")
    record = recovery.load_project(metadata)
    assert record is not None
    assert record.state is recovery.ProjectState.RESUMABLE
    assert [item.frame for item in record.checkpoints] == [2]
    assert project_root.exists()
    requests = [item[2] for item in scripted.log if item[0] == "tcmd"]
    assert "save_and_quit" in requests
    assert "delete" not in requests


def test_cancel_without_recovery_returns_unresumable_outcome(monkeypatch):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_sim = True
    session, _, _, _ = _run_session(monkeypatch, scripted=scripted)
    cancel = threading.Event()

    def emit(event):
        if event.phase == "SIMULATING":
            cancel.set()

    session._cancel = cancel
    session._emit = emit
    with pytest.raises(SessionCancelled) as raised:
        session.run()
    assert not raised.value.resumable
    outcome = raised.value.recovery_outcome
    assert outcome is not None
    assert not outcome.checkpoint_saved
    assert outcome.kind is RecoveryOutcomeKind.NOT_ENABLED


def test_cancel_before_simulation_skips_checkpoint(monkeypatch, tmp_path):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_build = True
    server_root = tmp_path / "server"
    metadata = tmp_path / "recovery" / "metadata.json"
    options = RecoveryOptions(
        True, metadata, _recovery_identity(), server_root,
        save_on_cancel=True)
    cancel = threading.Event()

    def emit(event):
        if event.phase == "BUILDING":
            cancel.set()

    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        cancel_event=cancel, emit=emit, poll_interval=0.001,
        recovery_options=options)
    with pytest.raises(SessionCancelled) as raised:
        session.run()
    outcome = raised.value.recovery_outcome
    assert outcome is not None
    assert not outcome.checkpoint_saved
    assert outcome.kind is RecoveryOutcomeKind.NOT_AVAILABLE_YET
    requests = [item[2] for item in scripted.log if item[0] == "tcmd"]
    assert "save_and_quit" not in requests
    assert "cancel_build" in requests


def test_cancel_with_failed_save_and_quit_marks_record_failed(
        monkeypatch, tmp_path):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_sim = True
    server_root = tmp_path / "server"
    (server_root / _scene().project_name).mkdir(parents=True)
    metadata = tmp_path / "recovery" / "metadata.json"
    options = RecoveryOptions(
        True, metadata, _recovery_identity(), server_root,
        save_on_cancel=True)
    cancel = threading.Event()

    def emit(event):
        if event.phase == "SIMULATING":
            cancel.set()

    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        cancel_event=cancel, emit=emit, poll_interval=0.001,
        simulate_timeout=0.05,
        recovery_options=options)
    with pytest.raises(SessionCancelled) as raised:
        session.run()
    outcome = raised.value.recovery_outcome
    assert outcome is not None
    assert not outcome.checkpoint_saved
    assert outcome.kind is RecoveryOutcomeKind.FAILED
    record = recovery.load_project(metadata)
    assert record is not None
    assert record.state is recovery.ProjectState.FAILED
    assert "timed out" in record.error
    assert record.checkpoints == ()


def test_cancel_with_connection_error_returns_structured_outcome(
        monkeypatch, tmp_path):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_sim = True
    server_root = tmp_path / "server"
    metadata = tmp_path / "recovery" / "metadata.json"
    options = RecoveryOptions(
        True, metadata, _recovery_identity(), server_root,
        save_on_cancel=True)
    cancel = threading.Event()
    original_tcmd = scripted._send_tcmd
    fail_recovery = False

    def emit(event):
        nonlocal fail_recovery
        if event.phase == "SIMULATING":
            fail_recovery = True
            cancel.set()

    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        cancel_event=cancel, emit=emit, poll_interval=0.001,
        recovery_options=options)

    def flaky_wire(address, config, project, request=None, *, frame=None):
        nonlocal fail_recovery
        if fail_recovery and request in ("save_and_quit", None):
            raise ClothNextError(ErrorRecord.create(
                category=ErrorCategory.SOLVER_CONNECTION,
                user_message="test", technical_message="test connection error",
                recommended_action="retry"))
        return original_tcmd(address, config, project, request, frame=frame)

    monkeypatch.setattr(wire, "send_tcmd", flaky_wire)
    with pytest.raises(SessionCancelled) as raised:
        session.run()
    outcome = raised.value.recovery_outcome
    assert outcome is not None
    assert not outcome.checkpoint_saved
    assert outcome.kind is RecoveryOutcomeKind.FAILED


def test_cancel_event_messages_include_recovery_events(monkeypatch, tmp_path):
    scripted = ScriptedWire(monkeypatch)
    scripted.hang_in_sim = True
    events = []
    cancel = threading.Event()

    def emit(event):
        events.append(event)
        if event.phase == "SIMULATING":
            cancel.set()

    server_root = tmp_path / "server"
    (server_root / _scene().project_name).mkdir(parents=True)
    metadata = tmp_path / "recovery" / "metadata.json"
    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        cancel_event=cancel, emit=emit, poll_interval=0.001,
        simulate_timeout=0.02,
        recovery_options=RecoveryOptions(
            True, metadata, _recovery_identity(), server_root,
            save_on_cancel=True))
    with pytest.raises(SessionCancelled):
        session.run()
    phases = [e.phase for e in events]
    assert "CANCELLING" in phases
    assert "RECOVERY_WARNING" in phases


def _cancel_recovery_session(tmp_path, *, existing_frame=None):
    server_root = tmp_path / "server"
    project_root = server_root / _scene().project_name
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "recovery" / "metadata.json"
    identity = _recovery_identity()
    record = recovery.create_project(
        metadata, project_id=_scene().project_name, identity=identity,
        server_data_root=server_root, project_root=project_root)
    record = recovery.transition(
        metadata, record, recovery.ProjectState.RUNNING)
    if existing_frame is not None:
        (output / f"state_{existing_frame}.bin.gz").write_bytes(
            gzip.compress(b"existing checkpoint"))
        record = recovery.confirm_saved_states(
            metadata, record, (existing_frame,), keep=3)
    options = RecoveryOptions(
        True, metadata, identity, server_root, save_on_cancel=True)
    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        poll_interval=0.001, simulate_timeout=0.01,
        recovery_options=options)
    session._recovery_record = record
    return session, metadata, output


@pytest.mark.parametrize("payload", [None, b"", b"not-gzip"])
def test_server_saved_state_requires_verified_checkpoint_file(
        monkeypatch, tmp_path, payload):
    session, metadata, output = _cancel_recovery_session(tmp_path)
    if payload is not None:
        (output / "state_4.bin.gz").write_bytes(payload)
    requests = []

    def reported_state(_address, _config, _project, request=None, **_kwargs):
        requests.append(request)
        return {"status": ("SAVE_AND_QUIT" if request else "RESUMABLE"),
                "saved_states": [4]}

    monkeypatch.setattr(wire, "send_tcmd", reported_state)
    outcome = session._save_recovery_on_cancel()
    assert outcome.kind is RecoveryOutcomeKind.FAILED
    assert not outcome.resumable
    assert outcome.saved_states == ()
    record = recovery.load_project(metadata)
    assert record is not None
    assert record.state is recovery.ProjectState.FAILED
    assert "verified checkpoint" in record.error


def test_existing_checkpoint_is_preserved_without_save_request(
        monkeypatch, tmp_path):
    session, _, _ = _cancel_recovery_session(tmp_path, existing_frame=3)
    requests = []

    def ready(_address, _config, _project, request=None, **_kwargs):
        requests.append(request)
        return {"status": "READY", "saved_states": [3]}

    monkeypatch.setattr(wire, "send_tcmd", ready)
    outcome = session._save_recovery_on_cancel()
    assert outcome.kind is RecoveryOutcomeKind.EXISTING_PRESERVED
    assert outcome.saved_states == (3,)
    assert "save_and_quit" not in requests


def test_save_and_quit_status_is_not_requested_twice(
        monkeypatch, tmp_path):
    session, _, output = _cancel_recovery_session(tmp_path)
    (output / "state_5.bin.gz").write_bytes(gzip.compress(b"new checkpoint"))
    calls = 0
    requests = []

    def already_saving(_address, _config, _project, request=None, **_kwargs):
        nonlocal calls
        calls += 1
        requests.append(request)
        return {"status": ("SAVE_AND_QUIT" if calls == 1 else "RESUMABLE"),
                "saved_states": ([] if calls == 1 else [5])}

    monkeypatch.setattr(wire, "send_tcmd", already_saving)
    outcome = session._save_recovery_on_cancel()
    assert outcome.kind is RecoveryOutcomeKind.SAVED
    assert outcome.saved_states == (5,)
    assert "save_and_quit" not in requests


def test_failed_new_checkpoint_preserves_older_verified_state(
        monkeypatch, tmp_path):
    session, metadata, _ = _cancel_recovery_session(
        tmp_path, existing_frame=2)
    status_calls = 0

    def no_new_file(_address, _config, _project, request=None, **_kwargs):
        nonlocal status_calls
        if request is None:
            status_calls += 1
        return {"status": ("SAVE_AND_QUIT" if request else
                           ("BUSY" if status_calls == 1 else "RESUMABLE")),
                "saved_states": ([2] if status_calls == 1 else [2, 6])}

    monkeypatch.setattr(wire, "send_tcmd", no_new_file)
    outcome = session._save_recovery_on_cancel()
    assert outcome.kind is RecoveryOutcomeKind.EXISTING_PRESERVED
    assert outcome.resumable
    assert outcome.saved_states == (2,)
    assert outcome.technical_reason
    record = recovery.load_project(metadata)
    assert record is not None
    assert [item.frame for item in record.checkpoints] == [2]
    assert record.error


def test_resume_skips_upload_build_and_fetches_only_missing_frames(
        monkeypatch, tmp_path):
    scripted = ScriptedWire(monkeypatch)
    server_root = tmp_path / "server"
    project_root = server_root / _scene().project_name
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    state = output / "state_3.bin.gz"
    state.write_bytes(gzip.compress(b"confirmed-state"))
    metadata = tmp_path / "recovery" / "metadata.json"
    identity = _recovery_identity()
    record = recovery.create_project(
        metadata, project_id=_scene().project_name, identity=identity,
        server_data_root=server_root, project_root=project_root)
    record = recovery.transition(
        metadata, record, recovery.ProjectState.RUNNING)
    record = recovery.confirm_saved_states(
        metadata, record, (3,), keep=3)
    record = recovery.transition(
        metadata, record, recovery.ProjectState.SAVED)
    recovery.transition(
        metadata, record, recovery.ProjectState.RESUMABLE)

    original = scripted._send_tcmd
    resumed = False

    def recovery_wire(address, config, project, request=None, *, frame=None):
        nonlocal resumed
        if request == "resume":
            scripted.log.append(("tcmd", project, request))
            resumed = True
            return {**scripted.base, "status": "BUSY", "frame": 3,
                    "saved_states": [3]}
        if request is None and not resumed:
            scripted.log.append(("tcmd", project, request))
            return {**scripted.base, "status": "RESUMABLE", "frame": 3,
                    "saved_states": [3]}
        if request is None and resumed:
            scripted.log.append(("tcmd", project, request))
            scripted.sim_polls += 1
            current = min(
                3 + scripted.sim_polls * scripted.frames_per_poll,
                scripted.solver_frames)
            return {
                **scripted.base,
                "status": ("READY" if current == scripted.solver_frames
                           else "BUSY"),
                "frame": current, "saved_states": [3]}
        response = original(address, config, project, request, frame=frame)
        return response

    monkeypatch.setattr(wire, "send_tcmd", recovery_wire)
    frames = []
    options = RecoveryOptions(
        True, metadata, identity, server_root, resume=True,
        completed_solver_frames=(1, 2, 3), keep_on_finish=True)
    session = SolverSession(
        resolved=_external_resolved(), scene=_scene(),
        work_directory=tmp_path / "run",
        external_address=wire.ServerAddress("127.0.0.1", 9999),
        frame_sink=frames.append, poll_interval=0.001,
        recovery_options=options)
    session.run()
    assert [frame.solver_frame for frame in frames] == [4, 5, 6, 7]
    assert not [item for item in scripted.log if item[0] == "upload"]
    requests = [item[2] for item in scripted.log if item[0] == "tcmd"]
    assert "build" not in requests
    assert "start" not in requests
    assert "resume" in requests


def test_import_result_playback_conversion():
    initial = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    world = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 2.0), (0, 0, 0, 1))
    # Solver world (Y-up): local (x, y, z) + z-offset 2 -> (x, z+2, -y)
    frames = [SolverFrame(1, ((0.0, 1.5, 0.0), (1.0, 1.5, 0.0)))]
    playback = import_result.build_playback_frames(initial, frames, world,
                                                   expected_frame_count=2)
    assert playback[0] == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    assert playback[1][0] == pytest.approx((0.0, 0.0, -0.5))
    assert playback[1][1] == pytest.approx((1.0, 0.0, -0.5))


def test_import_result_rejects_incomplete_or_duplicate_frames():
    initial = ((0.0, 0.0, 0.0),)
    world = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
    with pytest.raises(import_result.ImportValidationError, match="incomplete"):
        import_result.build_playback_frames(
            initial, [SolverFrame(2, ((0, 0, 0),))], world,
            expected_frame_count=3)
    with pytest.raises(import_result.ImportValidationError, match="incomplete"):
        import_result.build_playback_frames(
            initial, [SolverFrame(1, ((0, 0, 0),)),
                      SolverFrame(1, ((1, 1, 1),))], world,
            expected_frame_count=2)
    with pytest.raises(import_result.ImportValidationError,
                       match="constant topology"):
        import_result.build_playback_frames(
            initial, [SolverFrame(1, ((0, 0, 0), (1, 1, 1)))], world,
            expected_frame_count=2)


def test_new_bake_states_transition_paths():
    from cloth_next.bake.controller import BakeController
    from cloth_next.bake.status import BakeState
    controller = BakeController()
    for state in (BakeState.PREPARING, BakeState.EXPORTING,
                  BakeState.STARTING_SOLVER, BakeState.UPLOADING,
                  BakeState.BUILDING, BakeState.SIMULATING,
                  BakeState.FETCHING, BakeState.SIMULATING,
                  BakeState.FETCHING, BakeState.IMPORTING,
                  BakeState.FINISHED):
        controller.transition(state)
    # the preview path is still legal
    controller.transition(BakeState.PREPARING)
    controller.transition(BakeState.EXPORTING)
    controller.transition(BakeState.STARTING_SOLVER)
    controller.transition(BakeState.SIMULATING)
    # cancel from a real-run state
    controller.transition(BakeState.FETCHING)
    assert controller.request_cancel().state is BakeState.CANCELLING
    controller.transition(BakeState.CANCELLED)


def test_runtime_activity_reads_live_ppf_metric_files(tmp_path):
    scene = _scene()
    root = (tmp_path / "server-data" / scene.project_name / "session" /
            "output" / "data")
    root.mkdir(parents=True)
    (root / "advance.num_contact.out").write_text(
        "2.84 438\n2.85 408\n", encoding="ascii")
    (root / "advance.newton_steps.out").write_text(
        "2.85 2\n", encoding="ascii")
    (root / "advance.iter.out").write_text(
        "2.85 187\n", encoding="ascii")
    solver = SolverSession(
        resolved=_external_resolved(), scene=scene,
        work_directory=tmp_path,
        external_address=wire.ServerAddress("127.0.0.1", 9))

    code, message = solver._runtime_activity()

    assert code == "SOLVING_CONSTRAINTS"
    assert message == "Solver · 408 contacts · Newton 2 · 187 linear iterations"
    assert solver.diagnostics.contact_last == 408
