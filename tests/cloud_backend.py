# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic in-process Cloud backend (test double, NOT the real service).

Implements ``cloth_next.cloud.transport.CloudBackend`` with the semantics of
Cloud API v1 (pilot contract of 23 October 2026, ``tests/fixtures/cloud_v1``):
quotes in test units, idempotent create/submit with account-global keys,
one upload session per ``(role, name)``, account-only content reuse,
``offset_mismatch``, a server-computed ``manifest_sha256`` and raw results
under the v1 file names. Failures E23 cares about can be injected:

* ``lose_response`` - perform the next named operation, then raise
  ``CloudUnconfirmed`` (the answer was lost after the server acted);
* ``fail_before`` - raise ``CloudUnconfirmed`` before acting;
* ``offline`` - every call raises ``CloudError("network_unavailable")``;
* ``corrupt_download`` - flip one byte in the next downloaded block;
* ``cut_upload_after`` - drop the connection before a chunk crossing that
  byte count is confirmed.

Jobs run only when ``run_pending`` is called, through an ``executor``. The
default executor is synthetic; ``solver_executor`` runs the real local
GAIA/AETHER solver on the uploaded package.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import threading
import uuid

from cloth_next.cloud import transport as wire
from cloth_next.cloud.results import MAP_NAME, SURFACE_MAP_NAME, block_name

FRAMES_PER_BLOCK = 25


def manifest_sha256(manifest) -> str:
    """v1: SHA-256 of sorted-key compact UTF-8 JSON, no non-finite values."""
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class RawRunOutput:
    """What an executor returns: raw solver files."""

    def __init__(self, *, map_blob: bytes, frames: list[bytes],
                 surface_blob: bytes | None = None, build_id: str = "",
                 runtime_seconds: float | None = None, partial: bool = False,
                 failed_code: str = "") -> None:
        self.map_blob = map_blob
        self.surface_blob = surface_blob
        self.frames = frames
        self.build_id = build_id
        self.runtime_seconds = runtime_seconds
        self.partial = partial
        self.failed_code = failed_code


class CloudTestBackend:
    def __init__(self, *, executor=None, account: str = "account-a",
                 builds=None, quote_maximum: int = 30) -> None:
        self.lock = threading.Lock()
        self.account_name = account
        self.executor = executor
        # Offered desktop identities: dicts with engine, build_id,
        # solver_sha256, worker_sha256, server_build. None offers whatever a
        # manifest names (synthetic tests).
        self.desktop_builds = builds
        self.quote_maximum = quote_maximum
        # The server switch that unlocks Cloud Bakes in the add-on.
        self.execution_enabled = True
        self.can_submit = True
        self.gpu_type_id = "NVIDIA RTX A5000"
        self.blobs: dict[str, dict[str, bytes]] = {}       # account -> sha -> bytes
        self.uploads: dict[str, dict] = {}                 # upload_id -> session
        self.jobs: dict[str, dict] = {}                    # job_id -> job
        self.quotes: dict[str, dict] = {}
        self.idempotency: dict[tuple, tuple] = {}          # (account, key) -> (fp, job_id)
        self.calls: list[tuple] = []
        self.lose_response: set[str] = set()
        self.fail_before: set[str] = set()
        self.offline = False
        self.corrupt_download = False
        self.cut_upload_after: int | None = None
        self.expire_quotes = False
        self.partial_results = False   # v1: off until native validation exists
        self._ids = itertools.count(1)

    def _new_id(self) -> str:
        return str(uuid.UUID(int=next(self._ids)))

    # Injection helpers ------------------------------------------------------
    def _enter(self, name: str) -> None:
        self.calls.append((self.account_name, name))
        if self.offline:
            raise wire.CloudError("network_unavailable")
        if name in self.fail_before:
            self.fail_before.discard(name)
            raise wire.CloudUnconfirmed(name)

    def _leave(self, name: str, value):
        if name in self.lose_response:
            self.lose_response.discard(name)
            raise wire.CloudUnconfirmed(name)
        return value

    def _account_blobs(self) -> dict[str, bytes]:
        return self.blobs.setdefault(self.account_name, {})

    def _job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None or job["account"] != self.account_name:
            raise wire.CloudError("not_found", status=404)
        return job

    def _info(self, job: dict) -> wire.JobInfo:
        return wire.JobInfo(
            job_id=job["job_id"], client_job_id=job["client_job_id"],
            state=job["state"], progress_frames=job.get("progress"),
            total_frames=job["total_frames"], expires_at=job.get("expires_at"),
            build_id=job["manifest"]["build_id"],
            error_code=job.get("error_code", "") or "",
            manifest_sha256=job["manifest_sha256"],
            gpu_type_id=self.gpu_type_id if job["state"] != wire.UPLOADING else "",
            display_name=job.get("display_name") or "",
            billing_reserved=(None if job["state"] in wire.TERMINAL_STATES | {wire.UPLOADING}
                              else self.quote_maximum),
            billing_charged=(7 if job["state"] in wire.TERMINAL_STATES else None),
            billing_finalized=job["state"] in wire.TERMINAL_STATES,
            billing_unit="credit",
            final_cost=("7 credits" if job["state"] in wire.TERMINAL_STATES else None),
            elapsed_ms=(21000 if job["state"] in wire.TERMINAL_STATES else None))

    def _claim_key(self, key: str, fingerprint: str, create):
        slot = (self.account_name, key)
        if slot in self.idempotency:
            previous, job_id = self.idempotency[slot]
            if previous != fingerprint:
                raise wire.CloudError("idempotency_conflict", status=409)
            return job_id
        job_id = create()
        self.idempotency[slot] = (fingerprint, job_id)
        return job_id

    def _offered(self, manifest) -> dict | None:
        if self.desktop_builds is None:
            return {"engine": manifest["engine"], "build_id": manifest["build_id"],
                    "solver_sha256": manifest["solver_sha256"],
                    "worker_sha256": manifest["worker_sha256"],
                    "server_build": manifest["build_id"]}
        for item in self.desktop_builds:
            if all(item[key] == manifest[key] for key in
                   ("engine", "build_id", "solver_sha256", "worker_sha256")):
                return item
        return None

    # CloudBackend -------------------------------------------------------------
    def capabilities(self):
        self._enter("capabilities")
        offered = tuple(self.desktop_builds or ())
        return wire.Capabilities(
            True, ("GAIA", "AETHER"), tuple(item["build_id"] for item in offered),
            1, 64 * 1024 ** 3, retention_days=14, billing_mode="test",
            execution_enabled=self.execution_enabled,
            desktop_builds=tuple(wire.DesktopBuild(
                item["engine"], item["build_id"], item["solver_sha256"],
                item["worker_sha256"], item.get("server_build", item["build_id"]))
                for item in offered))

    def account(self):
        self._enter("account")
        return {"cloud_enabled": True, "execution_entitled": self.can_submit,
                "can_submit": self.can_submit, "billing": "test",
                "retained_results_require_execution_entitlement": False}

    def quote(self, manifest):
        self._enter("quote")
        if self._offered(manifest) is None:
            raise wire.CloudError("engine_build_unavailable", status=409)
        quote_id = self._new_id()
        digest = manifest_sha256(manifest)
        self.quotes[quote_id] = {"manifest_sha256": digest,
                                 "maximum": self.quote_maximum}
        return wire.Quote(quote_id, self.quote_maximum, "credit",
                          "2026-10-23T12:10:00Z", "test", False,
                          self.quote_maximum, digest,
                          gpu_type_id=self.gpu_type_id, usage_percent=290)

    def create_job(self, *, client_job_id, manifest, idempotency_key, display_name=""):
        self._enter("create_job")
        if display_name and not 1 <= len(display_name) <= 128:
            raise wire.CloudError("validation_error", status=422)
        with self.lock:
            offered = self._offered(manifest)
            if offered is None:
                raise wire.CloudError("engine_build_unavailable", status=409)
            digest = manifest_sha256(manifest)

            def create():
                job_id = self._new_id()
                self.jobs[job_id] = {
                    "display_name": display_name or None,
                    "job_id": job_id, "client_job_id": client_job_id,
                    "account": self.account_name, "state": wire.UPLOADING,
                    "manifest": manifest, "manifest_sha256": digest,
                    "files": {(item["role"], item["name"]): item
                              for item in manifest["files"]},
                    "attached": set(),
                    "total_frames": manifest["frame_end"] - manifest["frame_start"],
                    "server_build": offered.get("server_build", manifest["build_id"]),
                    "submits": 0, "quote_id": None}
                return job_id
            job_id = self._claim_key(idempotency_key, f"create:{digest}", create)
            return self._leave("create_job", self._info(self.jobs[job_id]))

    def open_upload(self, job_id, role, name):
        self._enter("open_upload")
        job = self._job(job_id)
        item = job["files"].get((role, name))
        if item is None:
            raise wire.CloudError("invalid_file_role", status=400)
        if item["sha256"] in self._account_blobs():
            job["attached"].add((role, name))
            return wire.FileUploadState(role, name, item["size_bytes"],
                                        item["size_bytes"], True, "", True, True)
        for upload_id, session in self.uploads.items():
            if session["job_id"] == job_id and session["key"] == (role, name) \
                    and not session["completed"]:
                return self._session_state(upload_id)
        upload_id = self._new_id()
        self.uploads[upload_id] = {"job_id": job_id, "key": (role, name),
                                   "item": item, "buffer": bytearray(),
                                   "completed": False, "confirmed": False,
                                   "account": self.account_name}
        return self._session_state(upload_id)

    def _session(self, upload_id):
        session = self.uploads.get(upload_id)
        if session is None or session["account"] != self.account_name:
            raise wire.CloudError("not_found", status=404)
        return session

    def _session_state(self, upload_id):
        session = self._session(upload_id)
        role, name = session["key"]
        return wire.FileUploadState(role, name, session["item"]["size_bytes"],
                                    len(session["buffer"]), session["confirmed"],
                                    upload_id, False, session["completed"])

    def upload_status(self, upload_id):
        self._enter("upload_status")
        return self._session_state(upload_id)

    def upload_chunk(self, upload_id, offset, data):
        self._enter("upload_chunk")
        session = self._session(upload_id)
        buffer = session["buffer"]
        if offset + len(data) <= len(buffer) and \
                bytes(buffer[offset:offset + len(data)]) == bytes(data):
            return self._leave("upload_chunk", self._session_state(upload_id))
        if offset != len(buffer):
            raise wire.CloudError("offset_mismatch", status=409)
        if self.cut_upload_after is not None and \
                len(buffer) + len(data) > self.cut_upload_after:
            self.cut_upload_after = None
            raise wire.CloudUnconfirmed("connection dropped")
        buffer.extend(data)
        return self._leave("upload_chunk", self._session_state(upload_id))

    def complete_upload(self, upload_id):
        self._enter("complete_upload")
        session = self._session(upload_id)
        raw = bytes(session["buffer"])
        item = session["item"]
        if len(raw) != item["size_bytes"] or \
                hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise wire.CloudError("integrity_mismatch", status=400)
        session["completed"] = session["confirmed"] = True
        self._account_blobs()[item["sha256"]] = raw
        self.jobs[session["job_id"]]["attached"].add(session["key"])
        return self._leave("complete_upload", self._session_state(upload_id))

    def submit(self, job_id, *, quote_id, accepted_maximum, idempotency_key):
        self._enter("submit")
        with self.lock:
            job = self._job(job_id)
            if job["state"] != wire.UPLOADING and job["quote_id"] == quote_id:
                # A retry with the accepted quote returns the same job (v1).
                return self._leave("submit", self._info(job))
            quote = self.quotes.get(quote_id)

            def accept():
                if quote is None or quote["manifest_sha256"] != job["manifest_sha256"] \
                        or float(quote["maximum"]) != float(accepted_maximum) \
                        or self.expire_quotes:
                    raise wire.CloudError("invalid_quote", status=409)
                if set(job["files"]) - job["attached"]:
                    raise wire.CloudError("upload_incomplete", status=409)
                job["state"] = wire.QUEUED
                job["submits"] += 1
                job["quote_id"] = quote_id
                return job_id
            self._claim_key(idempotency_key, f"submit:{job_id}:{quote_id}", accept)
            return self._leave("submit", self._info(job))

    def get_job(self, job_id):
        self._enter("get_job")
        return self._info(self._job(job_id))

    def find_job(self, client_job_id):
        self._enter("find_job")
        for job in self.jobs.values():
            if job["account"] == self.account_name and job["client_job_id"] == client_job_id:
                return self._info(job)
        return None

    def list_jobs(self):
        self._enter("list_jobs")
        return tuple(self._info(job) for job in self.jobs.values()
                     if job["account"] == self.account_name)

    def cancel(self, job_id):
        self._enter("cancel")
        job = self._job(job_id)
        if job["state"] in (wire.UPLOADING, wire.QUEUED, wire.STARTING, wire.RUNNING):
            job["state"] = wire.CANCELLED
        return self._leave("cancel", self._info(job))

    def result_manifest(self, job_id):
        self._enter("result_manifest")
        job = self._job(job_id)
        allowed = {wire.SUCCEEDED} | ({wire.STOPPED_AT_LIMIT} if self.partial_results
                                      else set())
        if job["state"] not in allowed or "result" not in job:
            raise wire.CloudError("result_unavailable", status=410)
        return job["result_manifest"]

    def download_result(self, job_id, name, offset, length):
        self._enter("download_result")
        job = self._job(job_id)
        data = job["result"][name][offset:offset + length]
        if self.corrupt_download and data:
            self.corrupt_download = False
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        return data

    def fetch_package_file(self, job_id, sha256):
        self._enter("fetch_package_file")
        job = self._job(job_id)
        if not any(item["sha256"] == sha256 for item in job["files"].values()):
            raise wire.CloudError("not_found", status=404)
        return self.blobs[job["account"]][sha256]

    # Execution ---------------------------------------------------------------
    def package_files(self, job: dict) -> dict:
        """Execution inputs by (role, name); snapshot roles are withheld (v1)."""
        blobs = self.blobs[job["account"]]
        return {key: blobs[item["sha256"]] for key, item in job["files"].items()
                if key[0] not in ("import-snapshot", "snapshot-arrays")}

    def run_pending(self) -> None:
        for job in list(self.jobs.values()):
            if job["state"] != wire.QUEUED:
                continue
            job["state"] = wire.RUNNING
            output = self.executor(job, self.package_files(job))
            self._publish(job, output)

    def _publish(self, job: dict, output: RawRunOutput) -> None:
        from cloth_next.cloud.results import parse_block_name
        result = {MAP_NAME: output.map_blob}
        if output.surface_blob is not None:
            result[SURFACE_MAP_NAME] = output.surface_blob
        frames = output.frames
        for first in range(1, len(frames) + 1, FRAMES_PER_BLOCK):
            last = min(first + FRAMES_PER_BLOCK - 1, len(frames))
            result[block_name(first, last)] = b"".join(frames[first - 1:last])
        result["execution.json"] = b"{}"
        job["result"] = result
        job["progress"] = len(frames)
        if output.failed_code:
            job["state"] = wire.FAILED
            job["error_code"] = output.failed_code
        elif output.partial:
            job["state"] = wire.STOPPED_AT_LIMIT
        else:
            job["state"] = wire.SUCCEEDED
        files = []
        for name, data in sorted(result.items()):
            span = parse_block_name(name) or (None, None)
            kind = ("vertex_map" if name == MAP_NAME else
                    "surface_map" if name == SURFACE_MAP_NAME else
                    "execution_metadata" if name == "execution.json" else "frame_block")
            files.append(wire.ResultFile(name, hashlib.sha256(data).hexdigest(),
                                         len(data), *span, kind))
        job["result_manifest"] = wire.ResultManifest(
            job_id=job["job_id"], build_id=output.build_id or job["server_build"],
            snapshot_digest=job["manifest"]["import_snapshot_digest"],
            frame_count=len(frames), partial=job["state"] == wire.STOPPED_AT_LIMIT,
            files=tuple(files),
            runtime_seconds=output.runtime_seconds)


def solver_executor(resolved, work_root, build_id: str, *, report: dict | None = None):
    """Executor that runs the real solver on a job's package files.

    Mirrors the v1 adapter requirements: materialize ``<role>/<name>``,
    BUILD, apply ``official-bindings``, run with the hydro environment, and
    return the untouched raw output.
    """
    import time
    from pathlib import Path
    from cloth_next.cloud.package import HYDRO_MODEL_ENV, HYDRO_VOLUME_ENV
    from cloth_next.ppf import results as solver_results
    from cloth_next.ppf_run.session import (SessionDeformable, SessionScene,
                                            SolverSession, new_project_name)
    identity = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))

    def run(job, files):
        manifest = job["manifest"]
        work = Path(work_root) / job["job_id"]
        inputs = work / "input"
        for (role, name), raw in files.items():
            path = inputs / role / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (work / "run").mkdir(parents=True, exist_ok=True)
        by_role = {}
        for item in manifest["files"]:
            by_role.setdefault(item["role"], []).append(item)
        environment = {}
        if manifest["water_mode"] == "hydrodynamic":
            model = inputs / "hydro-model" / by_role["hydro-model"][0]["name"]
            environment = {HYDRO_MODEL_ENV: str(model),
                           HYDRO_VOLUME_ENV: str(inputs / "hydro-volume")}
        # SOLID surface reconstruction runs on the client from the snapshot;
        # the server only needs raw frames, so identity matrices suffice here.
        objects = tuple(SessionDeformable(item["uuid"], item["uuid"],
                                          item["vertex_count"],
                                          item["deformable_type"], identity)
                        for item in manifest["objects"])
        first = objects[0]
        data = inputs / "data" / by_role["data"][0]["name"]
        param = inputs / "param" / by_role["param"][0]["name"]
        bindings = by_role.get("official-bindings")
        scene = SessionScene(
            new_project_name(), first.name, first.uuid, first.vertex_count, "", "",
            manifest["frame_end"] - manifest["frame_start"] + 1, data, param,
            by_role["data"][0]["sha256"], by_role["param"][0]["sha256"],
            deformable_type=first.deformable_type, deformable_world_matrix=identity,
            deformables=objects,
            official_bridge_json=((inputs / "official-bindings" / bindings[0]["name"])
                                  .read_text(encoding="utf-8") if bindings else ""))
        raw = {}
        session = SolverSession(
            resolved=resolved, scene=scene, work_directory=work / "run",
            raw_output_sink=lambda path, blob: raw.__setitem__(path, blob),
            hydrodynamic_environment=environment)
        begun = time.monotonic()
        diagnostics = session.run()
        runtime = time.monotonic() - begun
        if report is not None:
            report.update(seconds=round(runtime, 2), executed_build_id=build_id,
                          solver_timings=dict(diagnostics.timings))
        count = manifest["frame_end"] - manifest["frame_start"]
        return RawRunOutput(
            map_blob=raw[solver_results.MAP_PATH],
            surface_blob=raw.get(solver_results.SURFACE_MAP_PATH),
            frames=[raw[solver_results.frame_file_path(i)] for i in range(1, count + 1)],
            build_id=build_id, runtime_seconds=runtime)
    return run
