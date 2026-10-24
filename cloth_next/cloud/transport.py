# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The backend interface Cloud Bakes need, shaped after Cloud API v1.

``cloud.http_v1`` maps this interface onto ``https://api.cloth-next.de
/v1/cloud`` (pilot contract, 23 October 2026); tests use the deterministic
in-process double in ``tests/cloud_backend.py``. Callers above this module
never see URLs, tokens or HTTP.

Two failure kinds matter for money (E23):

``CloudError``        the server answered with an error envelope; the code
                      is the server's fixed code (never its message).
``CloudUnconfirmed``  the request may have reached the server but no answer
                      arrived (timeout after sending, reset, 5xx without
                      body). The caller reconciles by ``client_job_id`` and
                      never assumes "not sent" or "free".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

# Server job states (v1). ``uploading`` jobs were created but not submitted:
# they start nothing and cost nothing.
UPLOADING = "uploading"
QUEUED = "queued"
STARTING = "starting"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLING = "cancelling"
CANCELLED = "cancelled"
STOPPED_AT_LIMIT = "stopped_at_limit"
EXPIRED = "expired"
AWAITING_UPLOAD = UPLOADING  # historical name used by older callers
SERVER_STATES = frozenset({UPLOADING, QUEUED, STARTING, RUNNING, SUCCEEDED,
                           FAILED, CANCELLING, CANCELLED, STOPPED_AT_LIMIT,
                           EXPIRED})
SUBMITTED_STATES = SERVER_STATES - {UPLOADING}
TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, CANCELLED, STOPPED_AT_LIMIT,
                             EXPIRED})

# Server codes from the v1 error envelope plus transport conditions of this
# client. Anything else becomes "unknown" (CNX-E200).
KNOWN_CODES = frozenset({
    # v1 envelope codes
    "bad_request", "integrity_mismatch", "invalid_chunk", "invalid_file_role",
    "invalid_storage_path", "idempotency_key_required", "water_mode_unsupported",
    "not_authenticated", "forbidden", "insufficient_scope", "session_expired",
    "not_found", "idempotency_conflict", "engine_build_unavailable",
    "invalid_state", "invalid_quote", "upload_incomplete", "offset_mismatch",
    "content_corrupt", "conflict", "upload_expired", "result_unavailable",
    "payload_too_large", "package_too_large", "invalid_content_type",
    "validation_error", "rate_limited", "account_limit", "feature_disabled",
    # client-side conditions
    "network_unavailable", "service_unavailable", "invalid_response",
    "hash_mismatch", "quote_expired", "build_unavailable", "job_not_found",
    "result_expired", "cloud_access_required", "insufficient_credits",
    "cost_limit_reached", "engine_unavailable", "schema_unsupported",
})
RETRYABLE_CODES = frozenset({"rate_limited", "service_unavailable",
                             "network_unavailable"})


class CloudError(Exception):
    def __init__(self, code: str, *, retry_after: float | None = None,
                 request_id: str = "", status: int = 0) -> None:
        self.code = code if code in KNOWN_CODES else "unknown"
        self.retry_after = retry_after
        self.request_id = request_id
        self.status = status
        super().__init__(self.code)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


class CloudUnconfirmed(Exception):
    """A state-changing request has an unknown outcome."""


@dataclass(frozen=True, slots=True)
class DesktopBuild:
    engine: str
    build_id: str
    solver_sha256: str
    worker_sha256: str
    server_build: str = ""
    water_modes: tuple[str, ...] = ()
    protocol: str = ""
    payload_schema: str = ""
    water_flow: bool = True


@dataclass(frozen=True, slots=True)
class Capabilities:
    enabled: bool                            # Cloud API usable at all
    engines: tuple[str, ...]
    builds: tuple[str, ...]                  # offered desktop build ids
    package_schema: int
    max_package_bytes: int
    retention_days: int | None = None
    min_addon_version: str = ""
    desktop_builds: tuple[DesktopBuild, ...] = ()
    raw_chunk_bytes: int = 4 * 1024 * 1024
    billing_mode: str = ""
    partial_results: bool = False
    execution_enabled: bool = False          # RunPod execution configured
    upload_encodings: tuple[str, ...] = ("identity",)
    max_file_bytes: int = 0                  # 0: no per-file limit declared

    def mapping_for(self, solver: Mapping[str, Any]) -> DesktopBuild | None:
        """The configured mapping for exactly this desktop build, if any.

        Engine, build id and both native hashes must match; protocol, payload
        schema and water support too when the runtime declares them.
        """
        for item in self.desktop_builds:
            if (item.engine == solver.get("engine")
                    and item.build_id == solver.get("build_id")
                    and item.solver_sha256 == solver.get("solver_sha256")
                    and item.worker_sha256 == solver.get("worker_sha256")
                    and (not item.protocol or item.protocol == solver.get("protocol"))
                    and (not item.payload_schema
                         or item.payload_schema == solver.get("payload_schema"))
                    and (item.water_flow or not solver.get("water_flow"))):
                return item
        return None


@dataclass(frozen=True, slots=True)
class Quote:
    quote_id: str
    maximum: float
    unit: str
    expires_at: str
    billing_mode: str
    real_charge: bool
    max_execution_seconds: float | None = None
    manifest_sha256: str = ""


@dataclass(frozen=True, slots=True)
class FileUploadState:
    role: str
    name: str
    size: int
    received: int
    confirmed: bool
    upload_id: str = ""
    reused: bool = False
    completed: bool = False


@dataclass(frozen=True, slots=True)
class JobInfo:
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
    manifest_sha256: str = ""
    deleted: bool = False

    def __post_init__(self) -> None:
        if self.state not in SERVER_STATES:
            raise CloudError("invalid_response")


@dataclass(frozen=True, slots=True)
class ResultFile:
    name: str
    sha256: str
    size: int
    frame_start: int | None = None
    frame_end: int | None = None
    kind: str = ""


@dataclass(frozen=True, slots=True)
class ResultManifest:
    job_id: str
    build_id: str                 # build that actually ran (server build)
    snapshot_digest: str
    frame_count: int              # complete solver frames (vert_1..N)
    partial: bool
    files: tuple[ResultFile, ...]
    runtime_seconds: float | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


class CloudBackend(Protocol):
    def capabilities(self) -> Capabilities: ...

    def account(self) -> Mapping[str, Any]: ...

    def quote(self, manifest: Mapping[str, Any]) -> Quote: ...

    def create_job(self, *, client_job_id: str, manifest: Mapping[str, Any],
                   idempotency_key: str) -> JobInfo: ...

    def open_upload(self, job_id: str, role: str, name: str) -> FileUploadState: ...

    def upload_status(self, upload_id: str) -> FileUploadState: ...

    def upload_chunk(self, upload_id: str, offset: int,
                     data: bytes) -> FileUploadState: ...

    def complete_upload(self, upload_id: str) -> FileUploadState: ...

    def submit(self, job_id: str, *, quote_id: str, accepted_maximum: float,
               idempotency_key: str) -> JobInfo: ...

    def get_job(self, job_id: str) -> JobInfo: ...

    def find_job(self, client_job_id: str) -> JobInfo | None: ...

    def list_jobs(self) -> tuple[JobInfo, ...]: ...

    def cancel(self, job_id: str) -> JobInfo: ...

    def result_manifest(self, job_id: str) -> ResultManifest: ...

    def download_result(self, job_id: str, name: str, offset: int,
                        length: int) -> bytes: ...

    def fetch_package_file(self, job_id: str, sha256: str) -> bytes: ...
