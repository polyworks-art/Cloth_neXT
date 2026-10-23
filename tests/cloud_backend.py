# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic in-process Cloud backend (test double, NOT the real service).

Implements ``cloth_next.cloud.transport.CloudBackend`` for the add-on side of
Phase 0 while the real ``/v1/cloud/`` contract is pending. It keeps state in
memory per account, deduplicates package files per account only (B5), honours
idempotency keys (B16) and can inject the failures E23 cares about:

* ``lose_response`` - perform the next named operation, then raise
  ``CloudUnconfirmed`` (the answer was lost after the server acted);
* ``fail_before`` - raise ``CloudUnconfirmed`` before acting (nothing happened);
* ``offline`` - every call raises ``CloudError("network_unavailable")``;
* ``corrupt_download`` - flip one byte in the next downloaded block.

Jobs run only when ``run_pending`` is called, through an ``executor``. The
default executor is synthetic; ``tools/cloud_phase0.py`` installs one that
runs the real local GAIA/AETHER solver on the uploaded package.
"""
from __future__ import annotations

import hashlib
import itertools
import threading

from cloth_next.cloud import transport as wire
from cloth_next.cloud.results import MAP_NAME, SURFACE_MAP_NAME, block_name
from cloth_next.cloud.snapshot import canonical_bytes

FRAMES_PER_BLOCK = 25


class RawRunOutput:
    """What an executor returns: raw solver files keyed by role."""

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
                 builds=("gaia:test", "aether:test")) -> None:
        self.lock = threading.Lock()
        self.account = account
        self.executor = executor
        self.builds = tuple(builds)
        self.blobs: dict[str, dict[str, bytes]] = {}     # account -> sha -> bytes
        self.partial_uploads: dict[tuple, bytearray] = {}
        self.jobs: dict[str, dict] = {}                   # job_id -> job
        self.idempotency: dict[tuple, tuple] = {}         # (account, key) -> (fingerprint, response)
        self.calls: list[tuple] = []
        self.lose_response: set[str] = set()
        self.fail_before: set[str] = set()
        self.offline = False
        self.corrupt_download = False
        self.cut_upload_after: int | None = None          # bytes accepted, then drop
        self._ids = itertools.count(1)

    # Injection helpers ------------------------------------------------------
    def _enter(self, name: str) -> None:
        self.calls.append((self.account, name))
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
        return self.blobs.setdefault(self.account, {})

    def _job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None or job["account"] != self.account:
            raise wire.CloudError("job_not_found")
        return job

    def _info(self, job: dict) -> wire.JobInfo:
        missing = tuple(sorted(sha for sha in job["files"]
                               if sha not in self._account_blobs()))
        return wire.JobInfo(
            job_id=job["job_id"], client_job_id=job["client_job_id"],
            state=job["state"], missing_files=missing,
            progress_frames=job.get("progress"), total_frames=job["total_frames"],
            queue_position=1 if job["state"] == wire.QUEUED else None,
            expires_at=job.get("expires_at"), build_id=job["build_id"],
            error_code=job.get("error_code", ""), display_name=job["display_name"])

    def _idempotent(self, key: str, fingerprint: str, create):
        slot = (self.account, key)
        if slot in self.idempotency:
            previous, response = self.idempotency[slot]
            if previous != fingerprint:
                raise wire.CloudError("idempotency_conflict")
            return response
        response = create()
        self.idempotency[slot] = (fingerprint, response)
        return response

    # CloudBackend -------------------------------------------------------------
    def capabilities(self):
        self._enter("capabilities")
        return wire.Capabilities(True, ("GAIA", "AETHER"), self.builds, 1,
                                 64 * 1024 ** 3, retention_days=14)

    def create_job(self, *, client_job_id, manifest, manifest_sha256,
                   idempotency_key, quote_id=None):
        self._enter("create_job")
        with self.lock:
            if manifest["solver"]["build_id"] not in self.builds:
                raise wire.CloudError("build_unavailable")

            def create():
                job_id = f"job-{next(self._ids):04d}"
                files = {item["sha256"]: item["size"] for item in manifest["files"]}
                raw_manifest = canonical_bytes(manifest)
                if hashlib.sha256(raw_manifest).hexdigest() != manifest_sha256:
                    raise wire.CloudError("hash_mismatch")
                self.jobs[job_id] = {
                    "job_id": job_id, "client_job_id": client_job_id,
                    "account": self.account, "state": wire.AWAITING_UPLOAD,
                    "manifest": manifest, "manifest_sha256": manifest_sha256,
                    "manifest_bytes": raw_manifest,
                    "files": files, "build_id": manifest["solver"]["build_id"],
                    "total_frames": manifest["frames"]["count"] - 1,
                    "display_name": manifest["display_name"], "submits": 0}
                return job_id
            job_id = self._idempotent(idempotency_key, manifest_sha256, create)
            return self._leave("create_job", self._info(self.jobs[job_id]))

    def upload_state(self, job_id, sha256):
        self._enter("upload_state")
        job = self._job(job_id)
        size = job["files"][sha256]
        if sha256 in self._account_blobs():
            return wire.FileUploadState(sha256, size, size, True)
        received = len(self.partial_uploads.get((self.account, sha256), b""))
        return wire.FileUploadState(sha256, size, received, False)

    def upload_block(self, job_id, sha256, offset, data):
        self._enter("upload_block")
        job = self._job(job_id)
        size = job["files"][sha256]
        buffer = self.partial_uploads.setdefault((self.account, sha256), bytearray())
        if offset == len(buffer):  # a repeated block is ignored (idempotent)
            if self.cut_upload_after is not None and \
                    len(buffer) + len(data) > self.cut_upload_after:
                keep = max(0, self.cut_upload_after - len(buffer))
                buffer.extend(data[:keep])
                self.cut_upload_after = None
                raise wire.CloudUnconfirmed("connection dropped")
            buffer.extend(data)
        return self._leave("upload_block", wire.FileUploadState(
            sha256, size, len(buffer), False))

    def complete_upload(self, job_id, sha256):
        self._enter("complete_upload")
        job = self._job(job_id)
        buffer = bytes(self.partial_uploads.pop((self.account, sha256), b""))
        if hashlib.sha256(buffer).hexdigest() != sha256:
            return wire.FileUploadState(sha256, job["files"][sha256], 0, False)
        self._account_blobs()[sha256] = buffer
        return self._leave("complete_upload", wire.FileUploadState(
            sha256, len(buffer), len(buffer), True))

    def submit(self, job_id, *, idempotency_key):
        self._enter("submit")
        with self.lock:
            job = self._job(job_id)

            def accept():
                if any(sha not in self._account_blobs() for sha in job["files"]):
                    raise wire.CloudError("upload_incomplete")
                job["state"] = wire.QUEUED
                job["submits"] += 1
                return job_id
            self._idempotent(idempotency_key, job_id, accept)
            return self._leave("submit", self._info(job))

    def get_job(self, job_id):
        self._enter("get_job")
        return self._info(self._job(job_id))

    def find_job(self, client_job_id):
        self._enter("find_job")
        for job in self.jobs.values():
            if job["account"] == self.account and job["client_job_id"] == client_job_id:
                return self._info(job)
        return None

    def list_jobs(self):
        self._enter("list_jobs")
        return tuple(self._info(job) for job in self.jobs.values()
                     if job["account"] == self.account)

    def cancel(self, job_id, *, idempotency_key):
        self._enter("cancel")
        job = self._job(job_id)
        if job["state"] in (wire.AWAITING_UPLOAD, wire.QUEUED, wire.RUNNING):
            job["state"] = wire.CANCELLED
        return self._leave("cancel", self._info(job))

    def result_manifest(self, job_id):
        self._enter("result_manifest")
        job = self._job(job_id)
        if job["state"] not in (wire.SUCCEEDED, wire.STOPPED_AT_LIMIT,
                                wire.CANCELLED, wire.FAILED) or "result" not in job:
            raise wire.CloudError("job_not_found")
        return job["result_manifest"]

    def download_block(self, job_id, name, offset, length):
        self._enter("download_block")
        job = self._job(job_id)
        data = job["result"][name][offset:offset + length]
        if self.corrupt_download and data:
            self.corrupt_download = False
            data = bytes([data[0] ^ 0xFF]) + data[1:]
        return data

    def fetch_package_file(self, job_id, sha256):
        self._enter("fetch_package_file")
        job = self._job(job_id)
        if sha256 == job["manifest_sha256"]:
            return job["manifest_bytes"]
        if sha256 not in job["files"]:
            raise wire.CloudError("job_not_found")
        return self._account_blobs()[sha256]

    # Execution ---------------------------------------------------------------
    def package_files(self, job: dict) -> dict[str, bytes]:
        """Package bytes by manifest role/name, as a server would read them."""
        blobs = self.blobs[job["account"]]
        files = {"manifest.json": job["manifest_bytes"]}
        for item in job["manifest"]["files"]:
            files[item["name"]] = blobs[item["sha256"]]
        return files

    def run_pending(self) -> None:
        for job in list(self.jobs.values()):
            if job["state"] != wire.QUEUED:
                continue
            job["state"] = wire.RUNNING
            output = self.executor(job, self.package_files(job))
            self._publish(job, output)

    def _publish(self, job: dict, output: RawRunOutput) -> None:
        result = {MAP_NAME: output.map_blob}
        if output.surface_blob is not None:
            result[SURFACE_MAP_NAME] = output.surface_blob
        frames = output.frames
        for first in range(1, len(frames) + 1, FRAMES_PER_BLOCK):
            last = min(first + FRAMES_PER_BLOCK - 1, len(frames))
            result[block_name(first, last)] = b"".join(frames[first - 1:last])
        job["result"] = result
        job["progress"] = len(frames)
        if output.failed_code:
            job["state"] = wire.FAILED
            job["error_code"] = output.failed_code
        else:
            job["state"] = (wire.STOPPED_AT_LIMIT if output.partial
                            else wire.SUCCEEDED)
        files = tuple(wire.ResultFile(
            name, hashlib.sha256(data).hexdigest(), len(data),
            *(parse if (parse := _block_range(name)) else (None, None)))
            for name, data in sorted(result.items()))
        snapshot_digest = job["manifest"]["snapshot_digest"]
        job["result_manifest"] = wire.ResultManifest(
            job_id=job["job_id"], build_id=output.build_id or job["build_id"],
            snapshot_digest=snapshot_digest, frame_count=len(frames),
            partial=bool(output.partial or output.failed_code), files=files,
            runtime_seconds=output.runtime_seconds)


def _block_range(name: str):
    from cloth_next.cloud.results import parse_block_name
    return parse_block_name(name)


def solver_executor(resolved, work_root, build_id: str, *, report: dict | None = None):
    """Executor that runs the real solver on a job's package files.

    Mirrors what a Cloud server has to do (docs/cloud/BACKEND_REQUIREMENTS.md):
    rebuild the SessionScene from manifest + files, hand prepared hydro
    volumes in as environment, run, and return the untouched raw output.
    """
    import json
    import time
    from pathlib import Path
    from cloth_next.cloud.package import HYDRO_MODEL_ENV, HYDRO_VOLUME_ENV
    from cloth_next.ppf import results as solver_results
    from cloth_next.ppf_run.session import (SessionDeformable, SessionScene,
                                            SolverSession, new_project_name)

    def run(job, files):
        manifest = json.loads(files["manifest.json"])
        work = Path(work_root) / job["job_id"]
        (work / "run").mkdir(parents=True)
        data, param = work / "data.bin", work / "param.bin"
        data.write_bytes(files["data"])
        param.write_bytes(files["param"])
        environment = {}
        if manifest["water"] == "hydrodynamic":
            volumes = work / "run" / "hydro" / "volumes"
            for name, raw in files.items():
                if name.startswith("hydro/"):
                    path = volumes / name[len("hydro/"):]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
            model = work / "run" / "hydro" / "model.json"
            model.write_bytes(files["hydro-model.json"])
            environment = {HYDRO_MODEL_ENV: str(model), HYDRO_VOLUME_ENV: str(volumes)}
        objects = tuple(SessionDeformable(
            item["uuid"], item["uuid"], item["vertex_count"], item["deformable_type"],
            tuple(tuple(row) for row in item["solver_world_matrix"]))
            for item in manifest["objects"])
        first = objects[0]
        hashes = {item["role"]: item["sha256"] for item in manifest["files"]}
        scene = SessionScene(
            new_project_name(), first.name, first.uuid, first.vertex_count, "", "",
            manifest["frames"]["count"], data, param, hashes["data"], hashes["param"],
            deformable_type=first.deformable_type,
            deformable_world_matrix=first.world_matrix, deformables=objects,
            official_bridge_json=files.get("official-bindings.json", b"").decode("utf-8"))
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
        count = manifest["frames"]["count"] - 1
        return RawRunOutput(
            map_blob=raw[solver_results.MAP_PATH],
            surface_blob=raw.get(solver_results.SURFACE_MAP_PATH),
            frames=[raw[solver_results.frame_file_path(i)] for i in range(1, count + 1)],
            build_id=build_id, runtime_seconds=runtime)
    return run
