# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``CloudBackend`` over HTTPS: Cloud API v1 (pilot contract, 23 Oct 2026).

Conventions of the PolyWorks account client (``polyworks.api``): one request
per connection, no redirects, bounded responses, errors branch on the
envelope's ``error.code`` only and server messages are never shown. Only the
configured origin is contacted (HTTPS, or plain HTTP for a loopback IP test
server); every download URL a session returns must be on that origin.

The bearer token comes from ``token_provider`` on the calling (worker)
thread for each request and is never stored, logged or returned. Download
session URLs carry a short-lived ``t`` query credential: they live only in
memory here and never reach the registry, logs or the UI.

Lost answers: a state-changing request that was sent but got no usable
answer (timeout, reset, 5xx without an envelope) raises ``CloudUnconfirmed``
so the caller reconciles by ``client_job_id`` instead of resending blindly.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit

from . import transport as wire

API_ORIGIN = "https://api.cloth-next.de"
BASE_PATH = "/v1/cloud"
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 60.0
MAX_JSON_BYTES = 8 * 1024 * 1024
USER_AGENT = "ClothNeXt-Blender-Cloud"
_MUTATING = frozenset({"POST", "PATCH", "DELETE"})


def validate_origin(url: str) -> str:
    """HTTPS origin, or plain HTTP only for a loopback IP test server."""
    import ipaddress
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if (not host or parts.username or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/")):
        raise ValueError("not a bare origin")
    loopback = False
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parts.scheme == "https" or (parts.scheme == "http" and loopback):
        return f"{parts.scheme}://{parts.netloc}"
    raise ValueError("the Cloud API must use HTTPS outside a loopback test server")


def _retry_after(value) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if 0 <= seconds <= 24 * 3600 else None


def _int(value, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int:
        raise wire.CloudError("invalid_response")
    return value


def _str(value, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise wire.CloudError("invalid_response")
    return value


def _job(value: Any) -> wire.JobInfo:
    if not isinstance(value, dict):
        raise wire.CloudError("invalid_response")
    progress = value.get("progress") or {}
    return wire.JobInfo(
        job_id=_str(value.get("job_id")),
        client_job_id=_str(value.get("client_job_id")),
        state=_str(value.get("state")),
        progress_frames=_int(progress.get("completed_frame"), optional=True),
        total_frames=_int(progress.get("total_frames"), optional=True),
        queue_position=_int(progress.get("queue_position"), optional=True),
        eta_seconds=progress.get("eta_seconds"),
        expires_at=_str(value.get("result_expires_at"), optional=True),
        error_code=_str(value.get("error_code"), optional=True) or "",
        manifest_sha256=_str(value.get("manifest_sha256"), optional=True) or "",
        deleted=bool(value.get("deleted", False)))


def _upload(value: Any) -> wire.FileUploadState:
    if not isinstance(value, dict):
        raise wire.CloudError("invalid_response")
    return wire.FileUploadState(
        role=_str(value.get("role")), name=_str(value.get("name")),
        size=_int(value.get("size_bytes"), optional=True) or 0,
        received=_int(value.get("received", value.get("offset"))),
        confirmed=bool(value.get("confirmed")),
        upload_id=_str(value.get("upload_id"), optional=True) or "",
        reused=bool(value.get("reused")), completed=bool(value.get("completed")))


def _capabilities(value: Any) -> wire.Capabilities:
    """``CapabilitiesOut`` (OpenAPI v1-contract-draft)."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise wire.CloudError("invalid_response")
    builds = []
    engines = []
    for runtime in value.get("engines") or ():
        if not isinstance(runtime, dict):
            raise wire.CloudError("invalid_response")
        name = _str(runtime.get("engine"))
        engines.append(name)
        for item in runtime.get("desktop_builds") or ():
            builds.append(wire.DesktopBuild(
                name, _str(item.get("build_id")), _str(item.get("solver_sha256")),
                _str(item.get("worker_sha256")), _str(runtime.get("server_build")),
                (), _str(runtime.get("protocol")), _str(runtime.get("payload_schema")),
                bool(runtime.get("water_flow"))))
    limits = value.get("limits")
    execution = value.get("execution")
    transport_info = value.get("transport")
    if not all(isinstance(item, dict) for item in (limits, execution, transport_info)):
        raise wire.CloudError("invalid_response")
    return wire.Capabilities(
        enabled=bool(value.get("enabled")),
        engines=tuple(engines), builds=tuple(item.build_id for item in builds),
        package_schema=max(value.get("package_schemas") or [1]),
        max_package_bytes=_int(limits.get("max_package_bytes")),
        desktop_builds=tuple(builds),
        raw_chunk_bytes=_int(limits.get("chunk_bytes")),
        billing_mode=_str(value.get("billing")),
        partial_results=bool(value.get("partial_results")),
        execution_enabled=bool(execution.get("enabled")),
        upload_encodings=tuple(transport_info.get("upload_encodings") or ("identity",)),
        max_file_bytes=_int(limits.get("max_file_bytes"), optional=True) or 0)


class HttpCloudBackend:
    def __init__(self, token_provider: Callable[[], str | None], *,
                 origin: str = API_ORIGIN, context: ssl.SSLContext | None = None,
                 compress: bool = True) -> None:
        self.origin = validate_origin(origin)
        self._token = token_provider
        self._context = context
        self._compress = compress
        self._sessions: dict[tuple[str, str], tuple[float, dict]] = {}
        self._encodings: tuple[str, ...] = ("identity", "zstd")
        self._lock = threading.Lock()

    # Transport ---------------------------------------------------------------
    def _connection(self, origin: str):
        parts = urlsplit(origin)
        if parts.scheme == "https":
            return http.client.HTTPSConnection(
                parts.hostname, parts.port, timeout=CONNECT_TIMEOUT,
                context=self._context or ssl.create_default_context())
        return http.client.HTTPConnection(parts.hostname, parts.port,
                                          timeout=CONNECT_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        token = self._token()
        if not token:
            raise wire.CloudError("session_expired")
        if any(c in token for c in "\r\n"):
            raise wire.CloudError("invalid_response")
        return {"Authorization": "Bearer " + token, "User-Agent": USER_AGENT,
                "Accept": "application/json"}

    def _send(self, method: str, target: str, *, body: bytes | None = None,
              headers: Mapping[str, str] | None = None, origin: str | None = None,
              limit: int = MAX_JSON_BYTES):
        origin = origin or self.origin
        request_headers = self._headers()
        request_headers.update(headers or {})
        connection = self._connection(origin)
        sent = False
        try:
            try:
                connection.connect()
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                raise wire.CloudError("network_unavailable") from exc
            connection.sock.settimeout(READ_TIMEOUT)
            try:
                connection.request(method, target, body=body, headers=request_headers)
                sent = True
                response = connection.getresponse()
                raw = response.read(limit + 1)
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                if method in _MUTATING and sent:
                    raise wire.CloudUnconfirmed(method) from exc
                raise wire.CloudError("network_unavailable") from exc
            status = response.status
            if len(raw) > limit:
                raise wire.CloudError("invalid_response", status=status)
            return status, raw, response
        finally:
            connection.close()

    def _json(self, method: str, path: str, body: Any = None, *,
              idempotency_key: str | None = None, expect=(200, 201)):
        payload = None
        headers = {}
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        status, raw, response = self._send(method, BASE_PATH + path, body=payload,
                                           headers=headers)
        if status in expect:
            if status == 204 or not raw:
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise wire.CloudError("invalid_response", status=status) from exc
        self._raise(method, status, raw, response)

    @staticmethod
    def _raise(method: str, status: int, raw: bytes, response) -> None:
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, ValueError):
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        request_id = error.get("request_id") if isinstance(error, dict) else ""
        retry = _retry_after(response.getheader("Retry-After"))
        if not isinstance(code, str) or not code:
            if status >= 500 and method in _MUTATING:
                # No envelope: an edge proxy answered; the request may have run.
                raise wire.CloudUnconfirmed(method)
            code = ("service_unavailable" if status >= 500 else
                    "not_authenticated" if status == 401 else "invalid_response")
        raise wire.CloudError(code, retry_after=retry, status=status,
                              request_id=request_id if isinstance(request_id, str)
                              and len(request_id) <= 64 else "")

    # CloudBackend ---------------------------------------------------------------
    def capabilities(self) -> wire.Capabilities:
        value = self._json("GET", "/capabilities")
        transport_info = value.get("transport") if isinstance(value, dict) else None
        if isinstance(transport_info, dict):
            for key in ("api_origin", "download_origin"):
                declared = transport_info.get(key)
                if declared is not None and validate_origin(declared) != self.origin:
                    raise wire.CloudError("invalid_response")
        capabilities = _capabilities(value)
        self._encodings = capabilities.upload_encodings
        return capabilities

    def account(self) -> Mapping[str, Any]:
        """``AccountOut``: cloud_enabled, execution_entitled, can_submit, billing."""
        value = self._json("GET", "/account")
        if not isinstance(value, dict) or not isinstance(value.get("can_submit"), bool):
            raise wire.CloudError("invalid_response")
        return value

    def quote(self, manifest):
        value = self._json("POST", "/quotes", {"manifest": manifest})
        if not isinstance(value, dict):
            raise wire.CloudError("invalid_response")
        return wire.Quote(
            quote_id=_str(value.get("quote_id")), maximum=_int(value.get("maximum")),
            unit=_str(value.get("unit")), expires_at=_str(value.get("expires_at")),
            billing_mode=_str(value.get("billing_mode"), optional=True) or "",
            real_charge=bool(value.get("real_charge", True)),
            max_execution_seconds=value.get("max_execution_seconds"),
            manifest_sha256=_str(value.get("manifest_sha256"), optional=True) or "")

    def create_job(self, *, client_job_id, manifest, idempotency_key):
        return _job(self._json("POST", "/jobs", {"manifest": manifest},
                               idempotency_key=idempotency_key))

    def open_upload(self, job_id, role, name):
        return _upload(self._json("POST", f"/jobs/{quote(job_id)}/uploads",
                                  {"role": role, "name": name}))

    def upload_status(self, upload_id):
        return _upload(self._json("GET", f"/uploads/{quote(upload_id)}"))

    def upload_chunk(self, upload_id, offset, data):
        raw = bytes(data)
        headers = {"Upload-Offset": str(int(offset)),
                   "Chunk-SHA256": hashlib.sha256(raw).hexdigest(),
                   "Content-Type": "application/octet-stream"}
        body = raw
        if self._compress and "zstd" in self._encodings:
            try:
                import zstandard
            except ImportError:
                zstandard = None
            if zstandard is not None:
                # One independent zstd frame per PATCH (v1); level 1 measured best.
                body = zstandard.ZstdCompressor(level=1).compress(raw)
                headers["Content-Encoding"] = "zstd"
        status, payload, response = self._send(
            "PATCH", f"{BASE_PATH}/uploads/{quote(upload_id)}", body=body,
            headers=headers)
        if status == 200:
            try:
                return _upload(json.loads(payload.decode("utf-8")))
            except (UnicodeDecodeError, ValueError) as exc:
                raise wire.CloudUnconfirmed("PATCH") from exc
        self._raise("PATCH", status, payload, response)

    def complete_upload(self, upload_id):
        return _upload(self._json("POST", f"/uploads/{quote(upload_id)}/complete"))

    def submit(self, job_id, *, quote_id, accepted_maximum, idempotency_key):
        return _job(self._json("POST", f"/jobs/{quote(job_id)}/submit",
                               {"quote_id": quote_id,
                                "accepted_maximum": int(accepted_maximum)},
                               idempotency_key=idempotency_key))

    def get_job(self, job_id):
        return _job(self._json("GET", f"/jobs/{quote(job_id)}"))

    def find_job(self, client_job_id):
        try:
            return _job(self._json("GET", f"/jobs/by-client-id/{quote(client_job_id)}"))
        except wire.CloudError as exc:
            if exc.code == "not_found":
                return None
            raise

    def list_jobs(self):
        jobs, after = [], None
        while True:
            path = "/jobs?limit=100" + (f"&after={quote(after)}" if after else "")
            value = self._json("GET", path)
            items = value.get("jobs") if isinstance(value, dict) else None
            if not isinstance(items, list):
                raise wire.CloudError("invalid_response")
            jobs.extend(_job(item) for item in items)
            after = value.get("next_after")
            if not after or not items:
                return tuple(jobs)

    def cancel(self, job_id):
        return _job(self._json("POST", f"/jobs/{quote(job_id)}/cancel"))

    # Sessions and downloads ------------------------------------------------
    def _session(self, job_id: str, kind: str, *, renew: bool = False) -> dict:
        key = (job_id, kind)
        with self._lock:
            cached = self._sessions.get(key)
        if cached is not None and not renew and cached[0] > time.monotonic():
            return cached[1]
        value = self._json("POST", f"/jobs/{quote(job_id)}/{kind}")
        if not isinstance(value, dict) or not isinstance(value.get("files"), list):
            raise wire.CloudError("invalid_response")
        for item in value["files"]:
            url = item.get("url") if isinstance(item, dict) else None
            parts = urlsplit(url or "")
            if (f"{parts.scheme}://{parts.netloc}" != self.origin or parts.username
                    or parts.password or any(c in (url or "") for c in "\r\n\t ")):
                raise wire.CloudError("invalid_response")
        with self._lock:
            # Sessions are short-lived; reuse for at most a minute.
            self._sessions[key] = (time.monotonic() + 60.0, value)
        return value

    def result_manifest(self, job_id):
        value = self._session(job_id, "result-session")
        manifest = value.get("manifest")
        if not isinstance(manifest, dict) or manifest.get("partial") not in (False, True):
            raise wire.CloudError("invalid_response")
        files = []
        for item in manifest.get("files") or ():
            files.append(wire.ResultFile(
                _str(item.get("name")), _str(item.get("sha256")),
                _int(item.get("size_bytes")), _int(item.get("first_frame"), optional=True),
                _int(item.get("last_frame"), optional=True), str(item.get("kind", ""))))
        return wire.ResultManifest(
            job_id=job_id, build_id=_str(manifest.get("build_id")),
            snapshot_digest=_str(manifest.get("import_snapshot_digest")),
            frame_count=_int(manifest.get("completed_frame_count")),
            partial=bool(manifest.get("partial")), files=tuple(files),
            extra={key: manifest.get(key) for key in
                   ("solver_sha256", "worker_sha256", "server_artifact_sha256",
                    "protocol") if key in manifest})

    def _download(self, job_id: str, kind: str, match, offset: int, length: int) -> bytes:
        for renew in (False, True):
            session = self._session(job_id, kind, renew=renew)
            item = next((entry for entry in session["files"] if match(entry)), None)
            if item is None:
                raise wire.CloudError("not_found")
            parts = urlsplit(item["url"])
            target = parts.path + (f"?{parts.query}" if parts.query else "")
            headers = {"Range": f"bytes={offset}-{offset + length - 1}",
                       "Accept": "application/octet-stream",
                       "Accept-Encoding": "identity"}
            status, raw, response = self._send("GET", target, headers=headers,
                                               limit=length)
            if status in (206, 200):
                if status == 200:
                    raw = raw[offset:offset + length] if offset else raw[:length]
                if response.getheader("Content-Encoding", "identity") not in ("identity", None):
                    raise wire.CloudError("invalid_response")
                return raw
            if status in (401, 403, 410) and not renew:
                continue  # the short-lived token expired: one new session
            self._raise("GET", status, raw, response)
        raise wire.CloudError("result_unavailable")

    def download_result(self, job_id, name, offset, length):
        return self._download(job_id, "result-session",
                              lambda entry: entry.get("name") == name, offset, length)

    def fetch_package_file(self, job_id, sha256):
        session = self._session(job_id, "package-session")
        item = next((entry for entry in session["files"]
                     if entry.get("sha256") == sha256), None)
        if item is None:
            raise wire.CloudError("not_found")
        size = int(item["size_bytes"])
        data = bytearray()
        while len(data) < size:
            block = self._download(job_id, "package-session",
                                   lambda entry: entry.get("sha256") == sha256,
                                   len(data), min(8 * 1024 * 1024, size - len(data)))
            if not block:
                raise wire.CloudError("invalid_response")
            data.extend(block)
        if hashlib.sha256(bytes(data)).hexdigest() != sha256:
            raise wire.CloudError("hash_mismatch")
        return bytes(data)
