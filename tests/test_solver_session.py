# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure session-service logic against a scripted wire layer (no sockets,
no solver, no bpy): lifecycle order, cancellation decisions, ownership
safety, result validation, and playback conversion."""

from __future__ import annotations

import re
import struct
import threading
from pathlib import Path

import pytest

from cloth_next.core.errors import ClothNextError
from cloth_next.ppf import wire
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.resolver import ResolvedSolver, SolverMode
from cloth_next.ppf.schema import cbor_codec
from cloth_next.ppf_run import import_result, session as session_module
from cloth_next.ppf_run.session import (SessionCancelled, SessionScene,
                                       SolverFrame, SolverSession,
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

    def _send_tcmd(self, _address, _config, project, request=None):
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
