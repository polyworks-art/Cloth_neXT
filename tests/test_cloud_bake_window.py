"""Bake window follows a Cloud job with server data only."""
from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace

import pytest

from cloth_next.bake.status import BakeSnapshot, BakeState
from cloth_next.cloud import monitor
from cloth_next.cloud.registry import JobRecord, LocalState
from companion.app import (BakeWindow, cloud_details, cloud_display,
                           progress_display_text)

NOW = 1_800_000_000.0


def record(state: LocalState, **changes) -> JobRecord:
    base = JobRecord(
        client_job_id="0f0e0d0c-0b0a-4908-8706-050403020100", state=state,
        display_name="Shirt Dunk", engine="GAIA", build_id="gaia-test",
        manifest_sha256="a" * 64, snapshot_digest="b" * 64,
        package_hashes=(), created_at=NOW - 125.0, updated_at=NOW,
        server_build_id="gaia-test-linux", quote_maximum=30.0,
        quote_unit="credits", quote_billing_mode="test", quote_accepted=True)
    return replace(base, **changes)


def snap(state, **kwargs):
    return monitor.snapshot_for(record(state, **kwargs.pop("rec", {})),
                                job_id="bake-1", now=NOW, **kwargs)


def test_every_cloud_snapshot_is_server_only():
    for state in LocalState:
        value = snap(state)
        assert value.execution == "CLOUD"
        assert value.job_id == "bake-1"
        assert not (value.solver_name or value.solver_version or value.solver_mode
                    or value.solver_device or value.solver_telemetry)
        assert value.elapsed_seconds == pytest.approx(125.0)
        assert value.cloud_details["JOB"] == "Shirt Dunk"


def test_waiting_for_approval_points_to_review_dialog():
    value = snap(LocalState.AWAITING_APPROVAL, rec={"quote_accepted": False})
    assert value.status_title == "Waiting for your confirmation"
    assert "Review & Send" in value.activity_label
    assert value.can_cancel
    assert value.cloud_details["COST_LIMIT"] == "30 credits (test)"
    assert value.cloud_details["SERVER_STATE"] == "not sent yet"


def test_upload_progress_uses_live_byte_counters():
    value = snap(LocalState.UPLOADING, upload_done=20_000_000,
                 upload_total=80_000_000)
    assert value.state is BakeState.UPLOADING
    assert (value.progress_current, value.progress_total) == (20_000_000, 80_000_000)
    assert value.cloud_details["UPLOAD"] == "20.0 / 80.0 MB"
    assert progress_display_text(value) == "Cloud · Uploading to Cloud · 25%"


def test_queue_and_running_show_server_values_and_eta():
    queued = snap(LocalState.QUEUED, rec={"server_state": "queued",
                                          "queue_position": 3, "eta_seconds": 90.0,
                                          "job_id": "job_123"})
    assert queued.activity_label == "Waiting · position 3"
    assert queued.cloud_details["QUEUE"] == "Position 3"
    assert queued.cloud_details["SERVER_JOB"] == "job_123"
    assert queued.estimated_remaining_seconds == 90.0
    running = snap(LocalState.RUNNING, rec={"server_state": "running",
                                            "progress_frames": 7, "total_frames": 23,
                                            "eta_seconds": 40.0})
    assert running.state is BakeState.SIMULATING
    assert (running.progress_current, running.progress_total) == (7, 23)
    assert running.cloud_details["FRAMES"] == "7 / 23"
    assert running.cloud_details["ENGINE"] == "GAIA"
    assert progress_display_text(running) == "Cloud · Frame 7 / 23"
    assert running.can_cancel


def test_server_cancelling_is_not_cancellable_again():
    value = snap(LocalState.RUNNING, rec={"server_state": "cancelling"})
    assert value.state is BakeState.CANCELLING
    assert not value.can_cancel


def test_success_downloads_then_finishes():
    fetching = snap(LocalState.SUCCEEDED, auto_download=True,
                    download_done=5, download_total=10)
    assert fetching.state is BakeState.FETCHING
    assert fetching.status_title == "Downloading result"
    assert fetching.cloud_details["DOWNLOAD"] == "0.0 / 0.0 MB"
    manual = snap(LocalState.SUCCEEDED, auto_download=False,
                  rec={"expires_at": "2026-10-31T00:00:00Z", "final_cost": "4 credits",
                       "billing_finalized": True})
    assert manual.state is BakeState.FINISHED
    assert "Cloud Jobs list" in manual.activity_label
    assert manual.cloud_details["FINAL_COST"] == "4 credits"
    assert manual.cloud_details["AVAILABLE_UNTIL"] == "2026-10-31T00:00:00Z"
    stored = snap(LocalState.STORED)
    assert stored.state is BakeState.FINISHED and not stored.can_cancel


def test_failures_carry_cloud_error_codes():
    failed = snap(LocalState.FAILED)
    assert failed.state is BakeState.ERROR
    assert failed.error_code.startswith("CNX-E2")
    assert "Cloud job" in failed.error_details
    limit = snap(LocalState.STOPPED_AT_LIMIT)
    assert limit.status_title == "Stopped at cost limit"
    assert snap(LocalState.CANCELLED).state is BakeState.CANCELLED


def test_window_finished_rules():
    assert monitor.window_finished(record(LocalState.STORED), auto_download=True)
    assert monitor.window_finished(record(LocalState.FAILED), auto_download=True)
    assert not monitor.window_finished(record(LocalState.SUCCEEDED), auto_download=True)
    assert monitor.window_finished(record(LocalState.SUCCEEDED), auto_download=False)
    assert not monitor.window_finished(record(LocalState.RUNNING), auto_download=False)


def test_cloud_fields_survive_transport_and_old_values_stay_local():
    value = snap(LocalState.RUNNING, rec={"progress_frames": 2, "total_frames": 4})
    back = BakeSnapshot.from_dict(value.to_transport_dict())
    assert cloud_display(back)
    assert dict(cloud_details(back))["FRAMES"] == "2 / 4"
    legacy = BakeSnapshot.from_dict({"state": "SIMULATING"})
    assert legacy.execution == "LOCAL" and legacy.cloud_details == {}
    hostile = BakeSnapshot.from_dict({"execution": "ROOT",
                                      "cloud_details": {"JOB": True, "X": "y" * 999}})
    assert hostile.execution == "LOCAL"
    assert hostile.cloud_details == {"X": "y" * 256}


def test_cloud_window_can_close_while_job_runs():
    closed = []
    window = SimpleNamespace(
        _last_snapshot=snap(LocalState.RUNNING), close=lambda: closed.append(1),
        root=SimpleNamespace(bell=lambda: closed.append("bell")))
    BakeWindow._request_window_close(window)
    assert closed == [1]
    window._last_snapshot = BakeSnapshot(state=BakeState.SIMULATING)
    BakeWindow._request_window_close(window)
    assert closed == [1, "bell"]


@pytest.mark.skipif(os.name != "nt", reason="needs a desktop Tk")
def test_real_window_shows_cloud_section_and_hides_solver(monkeypatch):
    monkeypatch.setenv("CLOTH_NEXT_COMPANION_TEST_MODE", "hidden")
    window = BakeWindow()
    try:
        window._toggle_details()
        window.show(snap(LocalState.RUNNING, rec={
            "server_state": "running", "progress_frames": 3, "total_frames": 6}))
        window.root.update_idletasks()
        assert window.root.title() == "Cloth NeXt Bake"
        assert window.cloud_stats_section.winfo_manager()
        assert not window.solver_stats_section.winfo_manager()
        assert window.cloud_stat_vars["FRAMES"].get() == "3 / 6"
        assert window.progress_text.get() == "Cloud · Frame 3 / 6"
        window.show(BakeSnapshot(state=BakeState.SIMULATING, current_frame=2,
                                 progress_current=1, progress_total=4))
        window.root.update_idletasks()
        assert window.root.title() == "Cloth NeXt Bake"
        assert window.solver_stats_section.winfo_manager()
        assert not window.cloud_stats_section.winfo_manager()
    finally:
        window.close()


# Blender side: the window follows the job after packaging ---------------------

def _manager(monkeypatch, view_jobs, *, auto=True):
    manager = __import__("cloth_next.blender.companion_manager", fromlist=["x"])
    runtime = __import__("cloth_next.blender.cloud_runtime", fromlist=["x"])
    from cloth_next.blender.cloud_runtime import CloudView
    calls = {"published": [], "live": [], "enqueue": [], "pause": 0}
    state = {"view": CloudView(jobs=tuple(view_jobs))}
    monkeypatch.setattr(runtime, "view", lambda: state["view"])
    monkeypatch.setattr(runtime, "set_live", calls["live"].append)
    monkeypatch.setattr(runtime, "enqueue",
                        lambda action, job="": calls["enqueue"].append((action, job)))
    def pause():
        calls["pause"] += 1
    monkeypatch.setattr(runtime, "pause_upload", pause)
    monkeypatch.setattr(manager, "_auto_download", lambda: auto)
    monkeypatch.setattr(manager, "running", lambda: True)
    monkeypatch.setattr(manager, "_server", SimpleNamespace(
        publish=calls["published"].append, connected=lambda: True))
    monkeypatch.setattr(manager, "_production_job_id", "bake-1")
    monkeypatch.setattr(manager, "_terminal_deadline", None)
    manager._cloud_follow = ""
    return manager, calls, state


def test_follow_publishes_server_snapshots_and_frees_blender(blender_env, monkeypatch):
    job = record(LocalState.RUNNING, server_state="running", progress_frames=2,
                 total_frames=8)
    manager, calls, _state = _manager(monkeypatch, [job])
    released = []
    monkeypatch.setattr(manager.modal_lock, "release",
                        lambda *args: released.append(args))
    assert manager.follow_cloud_job(job.client_job_id)
    assert calls["live"] == [True]
    # The packaging run's own FINISHED is not shown; the lock is released.
    manager._publish(BakeSnapshot(state=BakeState.FINISHED, job_id="bake-1"))
    assert calls["published"] == [] and released
    manager._cloud_pulse(100.0)
    sent = calls["published"][-1]
    assert sent.execution == "CLOUD" and sent.cloud_details["FRAMES"] == "2 / 8"
    assert manager._terminal_deadline is None


def test_stored_job_closes_window_failed_job_stays(blender_env, monkeypatch):
    stored = record(LocalState.STORED)
    manager, calls, state = _manager(monkeypatch, [stored])
    manager.follow_cloud_job(stored.client_job_id)
    manager._cloud_pulse(10.0)
    assert manager._terminal_deadline == pytest.approx(10.0 + manager.CLOUD_SUCCESS_GRACE)
    assert calls["live"][-1] is False
    failed = record(LocalState.FAILED)
    manager, calls, state = _manager(monkeypatch, [failed])
    manager.follow_cloud_job(failed.client_job_id)
    manager._cloud_pulse(10.0)
    assert manager._terminal_deadline is None
    assert calls["published"][-1].state is BakeState.ERROR


def test_window_cancel_pauses_upload_then_cancels_job(blender_env, monkeypatch):
    job = record(LocalState.UPLOADING)
    manager, calls, _state = _manager(monkeypatch, [job])
    manager.follow_cloud_job(job.client_job_id)
    manager._cloud_cancel()
    assert calls["pause"] == 1
    assert calls["enqueue"] == [("cancel", job.client_job_id)]


def test_new_local_bake_stops_following(blender_env, monkeypatch):
    job = record(LocalState.QUEUED)
    manager, calls, _state = _manager(monkeypatch, [job])
    manager.follow_cloud_job(job.client_job_id)
    manager._pending_request = None
    monkeypatch.setattr(manager, "_launch", lambda: (True, "reused", False))
    manager.begin_bake_mode(SimpleNamespace(
        job_id="local", mode=None, frame_start=1, frame_end=2))
    assert manager.following_cloud_job() == ""
    assert calls["live"][-1] is False
    manager._pending_request = None


def test_window_shows_gpu_runtime_and_only_settled_costs():
    settled = snap(LocalState.STORED, rec={
        "gpu_type_id": "NVIDIA RTX A5000", "billing_finalized": True,
        "final_cost": "98 credits", "elapsed_ms": 282000,
        "submitted_confirmed": True})
    assert settled.cloud_details["GPU"] == "NVIDIA RTX A5000"
    assert settled.cloud_details["RUNTIME"] == "04:42"
    assert settled.cloud_details["FINAL_COST"] == "98 credits"
    pending = snap(LocalState.SUCCEEDED, rec={"submitted_confirmed": True,
                                              "elapsed_ms": 1000})
    assert pending.cloud_details["FINAL_COST"] == "settling on server"
    assert "RUNTIME" not in pending.cloud_details
    quoted = snap(LocalState.AWAITING_APPROVAL, rec={"quote_gpu": "NVIDIA RTX A5000"})
    assert quoted.cloud_details["GPU"] == "NVIDIA RTX A5000"
    assert "FINAL_COST" not in quoted.cloud_details
