# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake-window status for one Cloud job, built only from server data.

The Bake window follows a Cloud job after packaging. Every value it shows
comes from the job record the server last confirmed (state, queue position,
frames, ETA, cost) plus this client's own upload/download byte counters. No
local solver identity or telemetry is ever filled in.
"""

from __future__ import annotations

import time

from ..bake.status import (CLOUD_DETAIL_ROWS, BakeJobKind, BakeSnapshot,
                           BakeState, normalized)
from . import errors
from .registry import JobRecord, LocalState

DETAIL_ROWS = CLOUD_DETAIL_ROWS

_UPLOAD = frozenset({LocalState.PACKAGED, LocalState.CREATE_REQUESTED,
                     LocalState.UPLOADING})
_CANCELLABLE = frozenset({LocalState.AWAITING_APPROVAL, LocalState.PACKAGED,
                          LocalState.CREATE_REQUESTED, LocalState.UPLOADING,
                          LocalState.UPLOAD_PAUSED, LocalState.QUEUED,
                          LocalState.RUNNING})
UPLOAD_STATES = _UPLOAD
# A paused job with one of these codes cannot be resumed as it is.
BLOCKING_CODES = frozenset({"build_unavailable", "water_mode_unsupported",
                            "cloud_access_required", "package_too_large"})
_BLOCKED_HELP = {
    "water_mode_unsupported": (
        "The Cloud runs this GAIA build without Water Flow for now.",
        "Bake on This Computer, or switch Water Flow off for a dry Cloud Bake."),
    "build_unavailable": (
        "The Cloud has no mapping for the selected solver build.",
        "Select the GAIA build the Cloud offers, or Bake on This Computer."),
    "cloud_access_required": (
        "The PolyWorks account may not submit Cloud jobs.",
        "Check Cloud access on your account page."),
    "package_too_large": (
        "The package exceeds the server's size limit.",
        "Shorten the frame range or reduce the scene, then Bake again."),
}
_BLOCKED_TITLES = {
    "water_mode_unsupported": "Water Flow is not offered in the Cloud yet",
    "build_unavailable": "This solver build is not offered in the Cloud",
    "cloud_access_required": "This account may not send Cloud Bakes",
    "package_too_large": "The Bake is too large for the Cloud",
}
SUCCESS_TERMINAL = frozenset({LocalState.STORED})
FAILURE_TERMINAL = frozenset({LocalState.FAILED, LocalState.CANCELLED,
                              LocalState.STOPPED_AT_LIMIT, LocalState.EXPIRED,
                              LocalState.DISCARDED})


def _megabytes(done: int, total: int) -> str:
    return f"{done / 1e6:.1f} / {total / 1e6:.1f} MB"


def window_finished(record: JobRecord, *, auto_download: bool) -> bool:
    """True once the window has nothing more to follow for this job."""
    if record.state in SUCCESS_TERMINAL or record.state in FAILURE_TERMINAL:
        return True
    if record.state is LocalState.UPLOAD_PAUSED and record.error_code in BLOCKING_CODES:
        return True
    # Without automatic download the result waits on the server; the
    # window's work ends there and the Jobs list offers Download.
    return record.state is LocalState.SUCCEEDED and not auto_download


def details(record: JobRecord, *, upload_done: int = 0, upload_total: int = 0,
            download_done: int = 0, download_total: int = 0) -> dict[str, str]:
    rows = {"JOB": record.display_name or record.client_job_id[:8]}
    rows["SERVER_STATE"] = (record.server_state.replace("_", " ")
                            if record.server_state else "not sent yet")
    if record.engine:
        rows["ENGINE"] = record.engine.upper()  # GAIA or AETHER, no build id
    gpu = record.gpu_type_id or record.quote_gpu
    if gpu:
        rows["GPU"] = gpu
    if record.elapsed_ms is not None and record.billing_finalized:
        from ..bake.status import format_duration
        rows["RUNTIME"] = format_duration(record.elapsed_ms / 1000.0)
    if record.queue_position:
        rows["QUEUE"] = f"Position {record.queue_position}"
    if record.total_frames:
        rows["FRAMES"] = f"{record.progress_frames or 0} / {record.total_frames}"
    total = upload_total or record.upload_bytes_total
    done = upload_done if upload_total else record.upload_bytes_done
    if total and (record.state in _UPLOAD or record.state is LocalState.UPLOAD_PAUSED):
        rows["UPLOAD"] = _megabytes(min(done, total), total)
    if download_total:
        rows["DOWNLOAD"] = _megabytes(min(download_done, download_total),
                                      download_total)
    if record.quote_maximum is not None:
        unit = record.quote_unit or "credit"
        if record.quote_maximum != 1 and not unit.endswith("s"):
            unit += "s"
        rows["COST_LIMIT"] = f"{record.quote_maximum:g} {unit}"
        if record.quote_max_seconds:
            from ..bake.status import format_duration
            rows["MAX_RUNTIME"] = format_duration(float(record.quote_max_seconds))
    if record.final_cost and record.billing_finalized:
        rows["FINAL_COST"] = record.final_cost
    elif record.state in SUCCESS_TERMINAL | FAILURE_TERMINAL | {LocalState.SUCCEEDED,
                                                                LocalState.DOWNLOADING}:
        if record.submitted_confirmed:
            rows["FINAL_COST"] = "settling on server"
    if record.expires_at:
        rows["AVAILABLE_UNTIL"] = record.expires_at
    if record.job_id:
        rows["SERVER_JOB"] = record.job_id
    return rows


def snapshot_for(record: JobRecord, *, job_id: str = "", upload_done: int = 0,
                 upload_total: int = 0, download_done: int = 0,
                 download_total: int = 0, auto_download: bool = True,
                 now: float | None = None) -> BakeSnapshot:
    """The Bake-window snapshot for ``record`` (Cloud execution)."""
    now = time.time() if now is None else now
    state = record.state
    if state is LocalState.SUCCEEDED and auto_download and download_total:
        state = LocalState.DOWNLOADING  # the registry row lags the transfer
    rows = details(record, upload_done=upload_done, upload_total=upload_total,
                   download_done=download_done, download_total=download_total)
    changes: dict = {}
    if state is LocalState.UPLOAD_PAUSED and record.error_code in BLOCKING_CODES:
        public = errors.cnx_code(record.error_code)
        title = _BLOCKED_TITLES[record.error_code]
        cause, action = _BLOCKED_HELP[record.error_code]
        return normalized(
            BakeSnapshot(), state=BakeState.ERROR, job_id=job_id or record.client_job_id,
            job_kind=BakeJobKind.BAKE, execution="CLOUD", cloud_details=rows,
            status_title=title, status_message=title, activity_label=title,
            error_code=public, error_summary=title,
            error_details=(f"Stage: Cloud quote\nCause: {cause} Nothing was sent; "
                           f"no credits were used.\nWhat to do: {action}"),
            elapsed_seconds=max(0.0, now - record.created_at), can_cancel=False,
            active_object_name=record.display_name)
    if state is LocalState.AWAITING_APPROVAL and record.quote_id:
        bake_state, title = BakeState.PREPARING, "Confirm the Cloud cost limit"
        unit = record.quote_unit or "credit"
        rate = (f" · 1 {unit} = {record.quote_usage_percent / 100:g} s"
                if record.quote_usage_percent else "")
        activity = f"Click Send to start{rate}"
        changes.update(cloud_action="APPROVE")
        rows["QUOTE_ID"] = record.quote_id        # not drawn; echoed on Send
        rows["CLIENT_JOB_ID"] = record.client_job_id
        limit = f"max {record.quote_maximum:g} {unit}s" if record.quote_maximum is not None else ""
        title = " · ".join(item for item in (limit, record.quote_gpu) if item) or title
    elif state in (LocalState.PACKAGED, LocalState.AWAITING_APPROVAL):
        bake_state, title = BakeState.PREPARING, "Requesting a Cloud quote"
        activity = "Asking the server for GPU and cost limit"
    elif state in _UPLOAD:
        total = upload_total or record.upload_bytes_total
        done = upload_done if upload_total else record.upload_bytes_done
        bake_state, title = BakeState.UPLOADING, "Uploading to Cloud"
        activity = (f"Uploading {_megabytes(min(done, total), total)}"
                    if total else "Uploading scene")
        changes.update(progress_current=min(done, total) if total else 0,
                       progress_total=total or None)
    elif state is LocalState.UPLOAD_PAUSED:
        bake_state, title = BakeState.UPLOADING, "Upload paused"
        activity = ("Not sent · confirm a new cost limit in Blender"
                    if not record.quote_accepted else
                    "Not sent · resume in the Cloud Jobs list")
    elif state in (LocalState.SUBMIT_REQUESTED, LocalState.UNCONFIRMED):
        bake_state, title = BakeState.STARTING_SOLVER, "Confirming with server"
        activity = "Possibly started · checking with the server"
    elif state is LocalState.QUEUED:
        bake_state, title = BakeState.STARTING_SOLVER, "Queued on server"
        activity = (f"Waiting · position {record.queue_position}"
                    if record.queue_position else "Waiting for a GPU")
    elif state is LocalState.RUNNING:
        if record.server_state == "cancelling":
            bake_state, title = BakeState.CANCELLING, "Cancelling on server"
            activity = "Cancelling Cloud job"
        else:
            bake_state = BakeState.SIMULATING
            title = ("Starting on server" if record.server_state == "starting"
                     else "Simulating on server")
            done, total = record.progress_frames or 0, record.total_frames
            activity = (f"Server frame {done} / {total}" if total
                        else "Simulating on server")
            changes.update(progress_current=done, progress_total=total or None)
    elif state is LocalState.SUCCEEDED:
        bake_state = BakeState.FETCHING if auto_download else BakeState.FINISHED
        title = "Finished on server"
        activity = ("Downloading result" if auto_download
                    else "Ready · download from the Cloud Jobs list")
        if record.total_frames:
            changes.update(progress_current=record.total_frames,
                           progress_total=record.total_frames)
    elif state is LocalState.DOWNLOADING:
        bake_state, title = BakeState.FETCHING, "Downloading result"
        activity = (f"Downloading {_megabytes(min(download_done, download_total), download_total)}"
                    if download_total else "Downloading result")
        changes.update(progress_current=min(download_done, download_total),
                       progress_total=download_total or None)
    elif state is LocalState.STORED:
        bake_state, title = BakeState.FINISHED, "Cloud result downloaded"
        activity = "Stored in Blender"
        changes.update(progress_current=1, progress_total=1)
    elif state in (LocalState.CANCELLED, LocalState.DISCARDED):
        bake_state, title, activity = (BakeState.CANCELLED, "Cloud job cancelled",
                                       "Cancelled")
    else:
        bake_state = BakeState.ERROR
        reason = {LocalState.STOPPED_AT_LIMIT: "cost_limit_reached",
                  LocalState.EXPIRED: "result_expired"}.get(
                      state, record.error_code or "solver_failed")
        public, _info, text = errors.describe(reason)
        title = {LocalState.STOPPED_AT_LIMIT: "Stopped at cost limit",
                 LocalState.EXPIRED: "Expired on server"}.get(
                     state, "Cloud job failed")
        activity = title
        changes.update(error_code=public, error_summary=title,
                       error_details=f"Stage: Cloud job\nCause: {text}")
    eta = record.eta_seconds if state in (LocalState.QUEUED,
                                          LocalState.RUNNING) else None
    return normalized(
        BakeSnapshot(), state=bake_state, job_id=job_id or record.client_job_id,
        job_kind=BakeJobKind.BAKE, execution="CLOUD", cloud_details=rows,
        status_title=title, status_message=activity, activity_label=activity,
        elapsed_seconds=max(0.0, now - record.created_at),
        estimated_remaining_seconds=eta,
        can_cancel=state in _CANCELLABLE and record.server_state != "cancelling",
        solver_name="", solver_version="", solver_mode="",
        active_object_name=record.display_name, **changes)
