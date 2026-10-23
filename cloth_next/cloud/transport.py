# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The backend interface Cloud Bakes need (concept section 15, B1-B17).

The PolyWorks ``/v1/cloud/`` contract is being built separately and is not
final. Until it is, everything above this module talks to ``CloudBackend``
only; tests use the deterministic in-process server in
``tests/cloud_backend.py``. An HTTP implementation will map this interface
onto the finalized contract without changing callers.

Two failure kinds matter for money (E23):

``CloudError``        the server answered with an error; nothing happened
                      unless the code says otherwise.
``CloudUnconfirmed``  the request may have reached the server but no answer
                      arrived (timeout after sending, reset, 5xx without
                      body). The caller must reconcile by ``client_job_id``
                      and never assume "not sent" or "free".

A backend never returns signed URLs or tokens to callers; authentication is
the backend's own business (token from the Vault, worker thread only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

# Server job states (B8). ``awaiting_upload`` jobs were created but not
# submitted: they start nothing and cost nothing (B6).
AWAITING_UPLOAD = "awaiting_upload"
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
STOPPED_AT_LIMIT = "stopped_at_limit"
EXPIRED = "expired"
SERVER_STATES = frozenset({AWAITING_UPLOAD, QUEUED, RUNNING, SUCCEEDED, FAILED,
                           CANCELLED, STOPPED_AT_LIMIT, EXPIRED})
SUBMITTED_STATES = SERVER_STATES - {AWAITING_UPLOAD}
TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, CANCELLED, STOPPED_AT_LIMIT,
                             EXPIRED})

# Error codes the add-on understands (B14). Anything else maps to "unknown".
KNOWN_CODES = frozenset({
    "cloud_access_required", "insufficient_credits", "quote_expired",
    "cost_limit_reached", "package_too_large", "engine_unavailable",
    "build_unavailable", "schema_unsupported", "upload_incomplete",
    "hash_mismatch", "idempotency_conflict", "job_not_found",
    "result_expired", "rate_limited", "feature_disabled",
    "session_expired", "network_unavailable", "service_unavailable",
    "invalid_response"})
RETRYABLE_CODES = frozenset({"rate_limited", "service_unavailable",
                             "network_unavailable"})


class CloudError(Exception):
    def __init__(self, code: str, *, retry_after: float | None = None,
                 request_id: str = "") -> None:
        self.code = code if code in KNOWN_CODES else "unknown"
        self.retry_after = retry_after
        self.request_id = request_id
        super().__init__(self.code)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


class CloudUnconfirmed(Exception):
    """A state-changing request has an unknown outcome."""


@dataclass(frozen=True, slots=True)
class Capabilities:  # B1
    enabled: bool
    engines: tuple[str, ...]
    builds: tuple[str, ...]
    package_schema: int
    max_package_bytes: int
    retention_days: int | None = None
    min_addon_version: str = ""


@dataclass(frozen=True, slots=True)
class FileUploadState:  # B6
    sha256: str
    size: int
    received: int
    confirmed: bool


@dataclass(frozen=True, slots=True)
class JobInfo:  # B4/B7/B8
    job_id: str
    client_job_id: str
    state: str
    missing_files: tuple[str, ...] = ()
    progress_frames: int | None = None
    total_frames: int | None = None
    queue_position: int | None = None
    eta_seconds: float | None = None
    expires_at: str | None = None
    build_id: str = ""
    error_code: str = ""
    partial_frames: int | None = None
    final_cost: str | None = None
    display_name: str = ""

    def __post_init__(self) -> None:
        if self.state not in SERVER_STATES:
            raise CloudError("invalid_response")


@dataclass(frozen=True, slots=True)
class ResultFile:  # B10
    name: str
    sha256: str
    size: int
    frame_start: int | None = None
    frame_end: int | None = None


@dataclass(frozen=True, slots=True)
class ResultManifest:  # B10/B11
    job_id: str
    build_id: str
    snapshot_digest: str
    frame_count: int          # complete solver frames available (vert_1..N)
    partial: bool
    files: tuple[ResultFile, ...]
    runtime_seconds: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


class CloudBackend(Protocol):
    def capabilities(self) -> Capabilities: ...

    def create_job(self, *, client_job_id: str, manifest: Mapping[str, Any],
                   manifest_sha256: str, idempotency_key: str,
                   quote_id: str | None = None) -> JobInfo: ...

    def upload_state(self, job_id: str, sha256: str) -> FileUploadState: ...

    def upload_block(self, job_id: str, sha256: str, offset: int,
                     data: bytes) -> FileUploadState: ...

    def complete_upload(self, job_id: str, sha256: str) -> FileUploadState: ...

    def submit(self, job_id: str, *, idempotency_key: str) -> JobInfo: ...

    def get_job(self, job_id: str) -> JobInfo: ...

    def find_job(self, client_job_id: str) -> JobInfo | None: ...

    def list_jobs(self) -> tuple[JobInfo, ...]: ...

    def cancel(self, job_id: str, *, idempotency_key: str) -> JobInfo: ...

    def result_manifest(self, job_id: str) -> ResultManifest: ...

    def download_block(self, job_id: str, name: str, offset: int,
                       length: int) -> bytes: ...

    def fetch_package_file(self, job_id: str, sha256: str) -> bytes: ...
