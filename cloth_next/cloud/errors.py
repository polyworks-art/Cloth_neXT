# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Map Cloud failures to trusted CNX codes; never show raw server text.

Server error codes (B14), the local exception codes of this package and the
job states all resolve to one ``CNX-E2xx`` row from ``core.error_codes``,
whose cause and action are the only texts the artist sees. A ``request_id``
may be appended for support; the server's own message never is.
"""

from __future__ import annotations

from ..core.error_codes import ERROR_CODES, ErrorCodeInfo

_BY_CODE = {
    "network_unavailable": "CNX-E201",
    "offline": "CNX-E201",
    "session_expired": "CNX-E202",
    "unauthorized": "CNX-E202",
    "cloud_access_required": "CNX-E203",
    "feature_disabled": "CNX-E203",
    "insufficient_credits": "CNX-E204",
    "cost_limit_reached": "CNX-E204",
    "quote_expired": "CNX-E205",
    "build_unavailable": "CNX-E206",
    "engine_unavailable": "CNX-E206",
    "build_unverifiable": "CNX-E206",
    "package_too_large": "CNX-E207",
    "schema_unsupported": "CNX-E207",
    "invalid_package": "CNX-E207",
    "water_mode_unsupported": "CNX-E207",
    "upload_paused": "CNX-E208",
    "upload_incomplete": "CNX-E208",
    "unconfirmed": "CNX-E209",
    "solver_failed": "CNX-E210",
    "cancelled": "CNX-E211",
    "result_expired": "CNX-E212",
    "hash_mismatch": "CNX-E213",
    "result_corrupt": "CNX-E213",
    "local_package_missing": "CNX-E214",
    "local_package_corrupt": "CNX-E214",
    "import_incompatible": "CNX-E215",
    "insufficient_disk_space": "CNX-E216",
    "idempotency_conflict": "CNX-E217",
    "rate_limited": "CNX-E218",
    "service_unavailable": "CNX-E218",
    # Cloud API v1 envelope codes
    "not_authenticated": "CNX-E202",
    "forbidden": "CNX-E203",
    "insufficient_scope": "CNX-E203",
    "account_limit": "CNX-E204",
    "invalid_quote": "CNX-E205",
    "engine_build_unavailable": "CNX-E206",
    "bad_request": "CNX-E207",
    "invalid_chunk": "CNX-E207",
    "invalid_file_role": "CNX-E207",
    "invalid_storage_path": "CNX-E207",
    "invalid_content_type": "CNX-E207",
    "idempotency_key_required": "CNX-E207",
    "payload_too_large": "CNX-E207",
    "validation_error": "CNX-E207",
    "upload_expired": "CNX-E208",
    "integrity_mismatch": "CNX-E213",
    "content_corrupt": "CNX-E213",
    "result_unavailable": "CNX-E212",
    # execution error codes of a finished job
    "worker_failed": "CNX-E210",
    "worker_lost": "CNX-E210",
    "native_solver_failed": "CNX-E210",
    "native_process_terminated": "CNX-E210",
    "result_storage_failed": "CNX-E210",
    "test_limit_reached": "CNX-E204",
}


def cnx_code(code: str) -> str:
    return _BY_CODE.get(str(code), "CNX-E200")


def describe(code: str, *, request_id: str = "") -> tuple[str, ErrorCodeInfo, str]:
    """``(cnx_code, info, text)`` for an internal or server error code."""
    public = cnx_code(code)
    info = ERROR_CODES[public]
    text = f"{info.cause}. {info.action}"
    if request_id and len(request_id) <= 64 and request_id.isprintable():
        text += f" (Request {request_id})"
    return public, info, text


def code_of(exc: BaseException) -> str:
    """Internal code for any exception raised by the Cloud modules."""
    from . import transport
    from .jobs import UploadPaused
    if isinstance(exc, transport.CloudUnconfirmed):
        return "unconfirmed"
    if isinstance(exc, UploadPaused):
        return "upload_paused"
    return str(getattr(exc, "code", "") or "unknown")
