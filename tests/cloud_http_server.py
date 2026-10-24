# SPDX-License-Identifier: GPL-3.0-or-later
"""Loopback HTTP server speaking Cloud API v1 over ``CloudTestBackend``.

Test double only. It reproduces the wire format of the v1 pilot contract
(paths, headers, JSON shapes of ``tests/fixtures/cloud_v1/examples.json``,
error envelope, Range downloads with session tokens, zstd PATCH bodies) so
``cloth_next.cloud.http_v1`` is exercised byte for byte. It is NOT the
PolyWorks backend.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

from cloth_next.cloud import transport as wire

from tests.cloud_backend import CloudTestBackend

TOKEN = "test-session-token"
_STATUS = {"not_found": 404, "idempotency_conflict": 409, "engine_build_unavailable": 409,
           "invalid_quote": 409, "upload_incomplete": 409, "offset_mismatch": 409,
           "invalid_state": 409, "integrity_mismatch": 400, "invalid_file_role": 400,
           "result_unavailable": 410, "upload_expired": 410, "rate_limited": 429,
           "feature_disabled": 503, "not_authenticated": 401}


def _job_out(info: wire.JobInfo) -> dict:
    return {"job_id": info.job_id, "client_job_id": info.client_job_id,
            "state": info.state, "created_at": "2026-10-23T12:00:00Z",
            "submitted_at": None, "finished_at": None, "deleted": False,
            "manifest_sha256": info.manifest_sha256,
            "progress": {"completed_frame": info.progress_frames,
                         "total_frames": info.total_frames,
                         "queue_position": None, "eta_seconds": None},
            "error_code": info.error_code or None,
            "result_available": info.state == wire.SUCCEEDED,
            "result_expires_at": info.expires_at, "uploaded_roles": [],
            "statistics": {"elapsed_ms": None}}


def _upload_out(state: wire.FileUploadState) -> dict:
    return {"upload_id": state.upload_id or None, "offset": state.received,
            "size_bytes": state.size, "reused": state.reused,
            "received": state.received, "confirmed": state.confirmed,
            "role": state.role, "name": state.name, "completed": state.completed,
            "expires_at": "2026-10-23T13:00:00Z"}


class CloudHttpServer:
    def __init__(self, backend: CloudTestBackend) -> None:
        self.backend = backend
        self.tokens: dict[str, tuple[str, str]] = {}   # t -> (job_id, scope)
        self.requests: list[tuple[str, str, dict]] = []
        self.drop_after_handling: set[str] = set()     # "POST /submit" style keys
        self.redirect_next = False
        self.foreign_url = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _reply(self, status, body=None, *, raw=None, headers=None):
                if raw is None and body is not None:
                    raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                if raw is not None:
                    self.send_header("Content-Type", "application/json"
                                     if body is not None else "application/octet-stream")
                    self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if raw:
                    self.wfile.write(raw)

            def _error(self, code, status=None):
                self._reply(status or _STATUS.get(code, 400),
                            {"error": {"code": code, "message": "server text never shown",
                                       "request_id": "req_test"}})

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _route(self, method):
                parts = urlsplit(self.path)
                outer.requests.append((method, parts.path, dict(self.headers)))
                backend = outer.backend
                if outer.redirect_next:
                    outer.redirect_next = False
                    return self._reply(302, headers={"Location": "https://evil.example/"})
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._error("not_authenticated")
                path = parts.path
                body = self._body()
                try:
                    payload = json.loads(body) if body and self.headers.get(
                        "Content-Type") == "application/json" else None
                    key = self.headers.get("Idempotency-Key")
                    result = self._dispatch(method, path, payload, body, key,
                                            parse_qs(parts.query))
                except wire.CloudError as exc:
                    return self._error(exc.code, exc.status or None)
                except wire.CloudUnconfirmed:
                    self.close_connection = True
                    return None
                if result is None:
                    return None
                status, out = result
                drop = f"{method} {path.rsplit('/', 1)[-1]}"
                if drop in outer.drop_after_handling:
                    outer.drop_after_handling.discard(drop)
                    self.close_connection = True  # performed, answer lost
                    return None
                return self._reply(status, out)

            def _dispatch(self, method, path, payload, body, key, query):
                backend = outer.backend
                base = "/v1/cloud"
                if not path.startswith(base):
                    raise wire.CloudError("not_found")
                path = path[len(base):]
                if method == "GET" and path == "/account":
                    return 200, backend.account()
                if method == "GET" and path == "/capabilities":
                    caps = backend.capabilities()
                    runtimes = []
                    for item in caps.desktop_builds:
                        runtimes.append({
                            "engine": item.engine, "solver_version": "test",
                            "desktop_builds": [{"build_id": item.build_id,
                                                "solver_sha256": item.solver_sha256,
                                                "worker_sha256": item.worker_sha256}],
                            "server_build": item.server_build,
                            "server_solver_sha256": "f" * 64,
                            "server_worker_sha256": "0" * 64,
                            "server_artifact_sha256": "e" * 64,
                            "bindings_bridge_sha256": "d" * 64,
                            "protocol": "0.26", "payload_schema": "2",
                            "water_flow": True, "partial_results": False})
                    return 200, {
                        "schema_version": 1, "enabled": True, "billing": "test",
                        "execution": {"provider": "runpod", "deployment": None,
                                      "enabled": outer.backend.execution_enabled,
                                      "native_execution_on_website": False},
                        "transport": {"api_origin": outer.origin,
                                      "download_origin": outer.origin,
                                      "upload_encodings": ["identity", "zstd"],
                                      "result_encoding": "identity", "zstd_level": 1},
                        "package_schemas": [1], "engines": runtimes,
                        "limits": {"manifest_bytes": 2097152, "chunk_bytes": 4194304,
                                   "wire_chunk_bytes": 4198400,
                                   "max_file_bytes": 1 << 34, "max_package_bytes": 1 << 36,
                                   "max_open_jobs": 10, "max_account_bytes": 1 << 37},
                        "partial_results": False}
                if method == "POST" and path == "/quotes":
                    quote = backend.quote(payload["manifest"])
                    return 201, {"quote_id": quote.quote_id,
                                 "manifest_sha256": quote.manifest_sha256,
                                 "maximum": quote.maximum, "unit": quote.unit,
                                 "max_execution_seconds": quote.max_execution_seconds,
                                 "expires_at": quote.expires_at,
                                 "billing_mode": quote.billing_mode,
                                 "real_charge": quote.real_charge}
                if method == "POST" and path == "/jobs":
                    if not key:
                        raise wire.CloudError("idempotency_key_required")
                    manifest = payload["manifest"]
                    return 201, _job_out(backend.create_job(
                        client_job_id=manifest["client_job_id"], manifest=manifest,
                        idempotency_key=key))
                match = re.fullmatch(r"/jobs/by-client-id/([^/]+)", path)
                if method == "GET" and match:
                    info = backend.find_job(match.group(1))
                    if info is None:
                        raise wire.CloudError("not_found")
                    return 200, _job_out(info)
                if method == "GET" and path == "/jobs":
                    return 200, {"jobs": [_job_out(info) for info in backend.list_jobs()],
                                 "next_after": None}
                match = re.fullmatch(r"/jobs/([^/]+)(?:/([a-z-]+))?", path)
                if match:
                    job_id, action = match.groups()
                    if method == "GET" and action is None:
                        return 200, _job_out(backend.get_job(job_id))
                    if method == "POST" and action == "uploads":
                        return 201, _upload_out(backend.open_upload(
                            job_id, payload["role"], payload["name"]))
                    if method == "POST" and action == "submit":
                        return 200, _job_out(backend.submit(
                            job_id, quote_id=payload["quote_id"],
                            accepted_maximum=payload["accepted_maximum"],
                            idempotency_key=key))
                    if method == "POST" and action == "cancel":
                        return 200, _job_out(backend.cancel(job_id))
                    if method == "POST" and action == "result-session":
                        return 200, self._result_session(job_id)
                    if method == "POST" and action == "package-session":
                        return 200, self._package_session(job_id)
                match = re.fullmatch(r"/jobs/([^/]+)/(results|files)/([0-9a-f]{64})", path)
                if method == "GET" and match:
                    return self._download(*match.groups(), query)
                match = re.fullmatch(r"/uploads/([^/]+)(/complete)?", path)
                if match:
                    upload_id, complete = match.groups()
                    if method == "GET" and not complete:
                        return 200, _upload_out(backend.upload_status(upload_id))
                    if method == "POST" and complete:
                        return 200, _upload_out(backend.complete_upload(upload_id))
                    if method == "PATCH" and not complete:
                        return 200, _upload_out(self._patch(upload_id, body))
                raise wire.CloudError("not_found")

            def _patch(self, upload_id, body):
                import hashlib
                if self.headers.get("Content-Type") != "application/octet-stream":
                    raise wire.CloudError("invalid_content_type", 415)
                raw = body
                if self.headers.get("Content-Encoding") == "zstd":
                    import zstandard
                    raw = zstandard.ZstdDecompressor().decompress(body)
                if hashlib.sha256(raw).hexdigest() != self.headers.get("Chunk-SHA256"):
                    raise wire.CloudError("invalid_chunk")
                return outer.backend.upload_chunk(
                    upload_id, int(self.headers["Upload-Offset"]), raw)

            def _token(self, job_id, scope):
                token = secrets.token_urlsafe(12)
                outer.tokens[token] = (job_id, scope)
                return token

            def _result_session(self, job_id):
                manifest = outer.backend.result_manifest(job_id)
                token = self._token(job_id, "results")
                origin = "https://evil.example" if outer.foreign_url else outer.origin
                return {"expires_at": "2026-10-23T12:05:00Z", "manifest": {
                    "schema_version": 1, "build_id": manifest.build_id,
                    "server_build": manifest.build_id,
                    "import_snapshot_digest": manifest.snapshot_digest,
                    "completed_frame": manifest.frame_count,
                    "completed_frame_count": manifest.frame_count,
                    "partial": manifest.partial, "protocol": "0.26",
                    "files": [{"name": item.name, "kind": item.kind, "sha256": item.sha256,
                               "size_bytes": item.size, "first_frame": item.frame_start,
                               "last_frame": item.frame_end} for item in manifest.files]},
                    "files": [{"role": None, "name": item.name, "sha256": item.sha256,
                               "size_bytes": item.size,
                               "url": f"{origin}/v1/cloud/jobs/{job_id}/results/"
                                      f"{item.sha256}?t={token}"}
                              for item in manifest.files]}

            def _package_session(self, job_id):
                job = outer.backend._job(job_id)
                token = self._token(job_id, "files")
                return {"expires_at": "2026-10-23T12:05:00Z",
                        "manifest_sha256": job["manifest_sha256"],
                        "import_snapshot_digest": job["manifest"]["import_snapshot_digest"],
                        "manifest": job["manifest"],
                        "files": [dict(item, url=f"{outer.origin}/v1/cloud/jobs/{job_id}/"
                                                 f"files/{item['sha256']}?t={token}")
                                  for item in job["manifest"]["files"]]}

            def _download(self, job_id, scope, sha256, query):
                token = (query.get("t") or [""])[0]
                if outer.tokens.get(token) != (job_id, scope):
                    raise wire.CloudError("forbidden", 403)
                job = outer.backend._job(job_id)
                if scope == "results":
                    data = next(value for name, value in job["result"].items()
                                if __import__("hashlib").sha256(value).hexdigest() == sha256)
                    if outer.backend.corrupt_download:
                        outer.backend.corrupt_download = False
                        data = bytes([data[0] ^ 0xFF]) + data[1:]
                else:
                    data = outer.backend.fetch_package_file(job_id, sha256)
                spec = self.headers.get("Range", "")
                match = re.fullmatch(r"bytes=(\d+)-(\d+)", spec)
                if not match:
                    return self._reply(200, raw=data, headers={"ETag": f'"{sha256}"'}) or None
                first, last = int(match.group(1)), int(match.group(2))
                if first >= len(data):
                    return self._reply(416, raw=b"") or None
                part = data[first:last + 1]
                self._reply(206, raw=part, headers={
                    "Content-Range": f"bytes {first}-{first + len(part) - 1}/{len(data)}",
                    "ETag": f'"{sha256}"', "Content-Encoding": "identity"})
                return None

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_PATCH(self):
                self._route("PATCH")

            def do_DELETE(self):
                self._route("DELETE")

        return Handler
