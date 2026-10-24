# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cloud job lifecycle: upload, submit, reconcile, refresh, download.

Pure and blocking; Blender runs it on a worker thread and only reads registry
snapshots on the main thread. The order follows Cloud API v1: capabilities
and quote, explicit approval of the quoted maximum (E10), create, one upload
session per ``(role, name)``, submit with the accepted quote. Every
money-relevant step follows E23:

* idempotency keys are created with the record, before any network call;
* the intent is persisted before ``create_job`` / ``submit`` is sent;
* an unknown outcome becomes ``UNCONFIRMED`` and is resolved only by asking
  the server for ``client_job_id``: adopt a submitted job, re-submit with the
  same key, or re-create with the same ``client_job_id``. Never a new id.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Callable

from . import transport as wire
from .package import CloudPackage
from .package_store import BlobCorrupt, BlobMissing, PackageStore
from .registry import (JobRecord, JobRegistry, LocalState, NOT_SUBMITTED,
                       POSSIBLY_SUBMITTED, new_record)
from .results import ResultArchive, ResultCorrupt
from .snapshot import load_snapshot
from ..core.logging import get_logger

_LOG = get_logger("cloud.jobs")

UPLOAD_BLOCK = 8 * 1024 * 1024
BACKOFF = (2.0, 5.0, 15.0, 60.0)
PROGRESS_FLUSH_BYTES = 64 * 1024 * 1024
DOWNLOAD_RESERVE_RATIO = 0.10
DOWNLOAD_RESERVE_MIN = 1024 * 1024 * 1024

_SERVER_TO_LOCAL = {
    wire.QUEUED: LocalState.QUEUED,
    wire.STARTING: LocalState.RUNNING,
    wire.RUNNING: LocalState.RUNNING,
    wire.CANCELLING: LocalState.RUNNING,
    wire.SUCCEEDED: LocalState.SUCCEEDED,
    wire.FAILED: LocalState.FAILED,
    wire.CANCELLED: LocalState.CANCELLED,
    wire.STOPPED_AT_LIMIT: LocalState.STOPPED_AT_LIMIT,
    wire.EXPIRED: LocalState.EXPIRED,
}


class UploadPaused(Exception):
    """The upload stopped (cancel request or network); resumable."""


class ApprovalRequired(Exception):
    """No accepted, current quote: the artist must confirm a new maximum."""

    code = "quote_expired"


DISPLAY_NAME_LIMIT = 128
RATE_LIMIT_PATIENCE = 30 * 60.0   # total 429 waiting before an upload pauses
UPLOAD_WORKERS = 8          # measured: 1 → 10, 4 → 22, 8 → 34 Mbit/s (24 Oct)
SMALL_UPLOAD_BLOCK = 1_000_000  # fits a 1 MiB proxy body limit even uncompressed


def dashboard_name(name: str) -> str:
    """The job title for the Cloud Bakes dashboard, or "" to send none.

    One printable line of at most 128 characters; anything that looks like a
    path, URL or secret is not sent (the dashboard then shows its own title).
    """
    from . import privacy
    text = "".join(ch if ch.isprintable() else " " for ch in str(name or ""))
    text = " ".join(text.split())
    text = text[:DISPLAY_NAME_LIMIT].strip()
    if not text or privacy.findings({"display_name": text}):
        return ""
    return text


class AccessDenied(Exception):
    """The PolyWorks account may not submit Cloud jobs (``/account``)."""

    code = "cloud_access_required"


class PackageTooLarge(Exception):
    """The package exceeds the server's declared package or file limit."""

    code = "package_too_large"


class WaterFlowNotOffered(Exception):
    """The build is offered, but not with Water Flow (server runtime)."""

    code = "water_mode_unsupported"


class NotRetryable(Exception):
    """The server does not offer a free retry for this job."""

    code = "not_retryable"


def retry_client_id(client_job_id: str) -> str:
    """The server's deterministic id of the single retry of a job."""
    import uuid
    return str(uuid.uuid5(uuid.UUID(client_job_id), "cloud-bake-retry"))


class BuildNotOffered(Exception):
    """The Cloud has no configured mapping for this exact desktop build."""

    code = "build_unavailable"


class InsufficientDownloadSpace(Exception):
    code = "insufficient_disk_space"

    def __init__(self, required: int, available: int) -> None:
        super().__init__(f"{required - available} bytes more free space are needed")
        self.required = required
        self.available = available


@dataclass
class Progress:
    done: int = 0
    total: int = 0


class CloudJobs:
    def __init__(self, backend: wire.CloudBackend, registry: JobRegistry,
                 store: PackageStore, archive: ResultArchive, *,
                 sleep: Callable[[float], None] = time.sleep,
                 backoff: tuple[float, ...] = BACKOFF,
                 upload_block: int = UPLOAD_BLOCK,
                 free_bytes: Callable[[], int] | None = None) -> None:
        self.backend = backend
        self.registry = registry
        self.store = store
        self.archive = archive
        self._sleep = sleep
        self._backoff = backoff
        self._upload_block = upload_block
        import threading
        self._block_lock = threading.Lock()
        self._free_bytes = free_bytes

    # Registration ----------------------------------------------------------
    def register(self, package: CloudPackage, *, blend_identity: str = "") -> JobRecord:
        return self.registry.add(new_record(
            client_job_id=package.client_job_id,
            display_name=package.display_name or package.client_job_id,
            solver=package.solver,
            manifest_sha256=package.manifest_sha256,
            snapshot_digest=package.snapshot.digest,
            package_hashes=package.hashes,
            upload_bytes_total=package.total_bytes,
            blend_identity=blend_identity,
            object_uuids=[item["uuid"] for item in package.manifest["objects"]]))

    # List cleanup ------------------------------------------------------------
    def prune(self) -> int:
        """Remove list entries whose server job no longer exists.

        Discarded (never sent) entries go too. A job with a downloaded result
        on this computer stays, whatever the server says: the result is local.
        """
        gone = []
        for record in self.registry.all():
            if record.state is LocalState.DISCARDED:
                gone.append(record.client_job_id)
                continue
            if not record.job_id or record.state is LocalState.STORED:
                continue
            try:
                info = self.backend.get_job(record.job_id)
            except wire.CloudError as exc:
                if exc.code in ("not_found", "job_not_found"):
                    gone.append(record.client_job_id)
                continue
            except wire.CloudUnconfirmed:
                continue
            if info.deleted:
                gone.append(record.client_job_id)
        for client_job_id in gone:
            self.store.release_reference(client_job_id)
        return self.registry.remove(gone)

    # Retry of a free infrastructure failure ----------------------------------
    def request_retry(self, client_job_id: str) -> JobRecord:
        """Quote the retry of a failed job; the artist confirms it (E10).

        The retry is a new server job with a deterministic client id that
        reuses the retained package, so nothing is uploaded again. Locally it
        is a new job record waiting for approval; approving it sends
        ``POST /jobs/{job_id}/retry`` with exactly the quoted maximum.
        """
        parent = self.registry.get(client_job_id)
        if parent is None or not parent.job_id or not parent.retryable:
            raise NotRetryable(client_job_id)
        child_id = retry_client_id(client_job_id)
        child = self.registry.get(child_id)
        if child is not None and child.submitted_confirmed:
            return child
        manifest = dict(load_manifest(self.store, parent.manifest_sha256))
        manifest["client_job_id"] = child_id
        from .package import canonical_bytes
        blob = self.store.put_bytes(canonical_bytes(manifest))
        if child is None:
            from .registry import new_record
            child = self.registry.add(replace(new_record(
                client_job_id=child_id, display_name=parent.display_name,
                solver={"engine": parent.engine, "build_id": parent.build_id},
                manifest_sha256=blob.sha256, snapshot_digest=parent.snapshot_digest,
                package_hashes=parent.package_hashes,
                upload_bytes_total=0, blend_identity=parent.blend_identity,
                object_uuids=parent.object_uuids), retry_of=client_job_id,
                server_build_id=parent.server_build_id))
            self.store.add_reference(child_id, tuple(parent.package_hashes) + (blob.sha256,))
        self.registry.update(client_job_id, retried_as=child_id)
        quote = self.backend.quote(manifest)
        return self.registry.update(
            child_id, state=LocalState.AWAITING_APPROVAL,
            quote_id=quote.quote_id, quote_maximum=quote.maximum,
            quote_unit=quote.unit, quote_expires_at=quote.expires_at,
            quote_billing_mode=quote.billing_mode, quote_accepted=False,
            quote_max_seconds=quote.max_execution_seconds,
            quote_gpu=quote.gpu_type_id, quote_usage_percent=quote.usage_percent,
            error_code="")

    def _submit_retry(self, record: JobRecord) -> JobRecord:
        parent = self.registry.get(record.retry_of)
        record = self.registry.update(record.client_job_id,
                                      state=LocalState.SUBMIT_REQUESTED)
        try:
            info = self.backend.retry(parent.job_id,
                                      accepted_maximum=int(record.quote_maximum))
        except wire.CloudUnconfirmed:
            self.registry.update(record.client_job_id, state=LocalState.UNCONFIRMED)
            return self.reconcile(record.client_job_id)
        except wire.CloudError as exc:
            if exc.code in ("invalid_quote", "quote_expired"):
                self.registry.update(record.client_job_id, quote_accepted=False,
                                     state=LocalState.UPLOAD_PAUSED,
                                     error_code="quote_expired")
            else:
                self.registry.update(record.client_job_id,
                                     state=LocalState.UPLOAD_PAUSED,
                                     error_code=exc.code)
            raise
        updated = self._adopt(record, info)
        self.registry.update(parent.client_job_id, retryable=False)
        return updated

    # Quote + approval (E10) ------------------------------------------------
    def request_quote(self, client_job_id: str) -> JobRecord:
        """Check the build mapping, get a binding quote, wait for approval."""
        record = self.registry.get(client_job_id)
        manifest = load_manifest(self.store, record.manifest_sha256)
        # The artist is waiting for this answer: no silent backoff loop.
        capabilities = self.backend.capabilities()
        if capabilities.raw_chunk_bytes:
            self._upload_block = min(self._upload_block, capabilities.raw_chunk_bytes)
        account = self.backend.account()
        if not (account.get("cloud_enabled") and account.get("can_submit")):
            self.registry.update(client_job_id, state=LocalState.UPLOAD_PAUSED,
                                 error_code="cloud_access_required")
            raise AccessDenied("this account may not submit Cloud jobs")
        mapping = capabilities.mapping_for(manifest)
        if mapping is None:
            dry = capabilities.mapping_for({**manifest, "water_flow": False})
            code = "water_mode_unsupported" if dry is not None else "build_unavailable"
            self.registry.update(client_job_id, state=LocalState.UPLOAD_PAUSED,
                                 error_code=code)
            if dry is not None:
                raise WaterFlowNotOffered(manifest["build_id"])
            raise BuildNotOffered(manifest["build_id"])
        sizes = [int(item["size_bytes"]) for item in manifest["files"]]
        if ((capabilities.max_package_bytes and sum(sizes) > capabilities.max_package_bytes)
                or (capabilities.max_file_bytes
                    and max(sizes, default=0) > capabilities.max_file_bytes)):
            self.registry.update(client_job_id, state=LocalState.UPLOAD_PAUSED,
                                 error_code="package_too_large")
            raise PackageTooLarge(f"{sum(sizes)} bytes")
        quote = self.backend.quote(manifest)
        return self.registry.update(
            client_job_id, state=LocalState.AWAITING_APPROVAL,
            server_build_id=mapping.server_build or mapping.build_id,
            quote_id=quote.quote_id, quote_maximum=quote.maximum,
            quote_unit=quote.unit, quote_expires_at=quote.expires_at,
            quote_billing_mode=quote.billing_mode, quote_accepted=False,
            quote_max_seconds=quote.max_execution_seconds,
            quote_gpu=quote.gpu_type_id, quote_usage_percent=quote.usage_percent,
            error_code="")

    def accept_quote(self, client_job_id: str, quote_id: str) -> JobRecord:
        """Main thread, after the artist confirmed exactly this quote."""
        record = self.registry.get(client_job_id)
        if record.quote_id != quote_id or record.quote_maximum is None:
            raise ApprovalRequired("the confirmed quote is not the current one")
        return self.registry.update(client_job_id, quote_accepted=True,
                                    state=LocalState.UPLOAD_PAUSED)

    # Helpers ---------------------------------------------------------------
    def _retry(self, call, *, cancelled=lambda: False):
        """Retry idempotent reads/uploads on transient errors, then pause."""
        attempt = 0
        while True:
            try:
                return call()
            except (wire.CloudUnconfirmed, wire.CloudError) as exc:
                transient = (isinstance(exc, wire.CloudUnconfirmed)
                             or exc.retryable)
                if not transient:
                    raise
                if cancelled():
                    raise UploadPaused("network") from exc
                if isinstance(exc, wire.CloudError) and exc.code == "rate_limited":
                    # The write budget (6000 per 15 min) refills: wait as told
                    # and continue; this is not a broken connection.
                    waited = getattr(self, "_rate_waited", 0.0)
                    delay = min(max(float(exc.retry_after or 30.0), 1.0), 900.0)
                    if waited + delay > RATE_LIMIT_PATIENCE:
                        raise UploadPaused("rate_limited") from exc
                    self._rate_waited = waited + delay
                    self._sleep(delay)
                    continue
                if attempt >= len(self._backoff):
                    raise UploadPaused("network") from exc
                delay = getattr(exc, "retry_after", None) or self._backoff[attempt]
                self._sleep(delay)
                attempt += 1

    def _adopt(self, record: JobRecord, info: wire.JobInfo, **changes) -> JobRecord:
        changes.update(
            job_id=info.job_id or record.job_id, server_state=info.state,
            progress_frames=info.progress_frames, total_frames=info.total_frames,
            queue_position=info.queue_position, eta_seconds=info.eta_seconds,
            expires_at=info.expires_at, error_code=info.error_code)
        if info.final_cost is not None:
            changes["final_cost"] = info.final_cost
        if info.gpu_type_id:
            changes["gpu_type_id"] = info.gpu_type_id
        changes.update(billing_reserved=info.billing_reserved,
                       billing_finalized=info.billing_finalized)
        if info.elapsed_ms is not None:
            changes["elapsed_ms"] = info.elapsed_ms
        changes["retryable"] = info.retryable
        local = _SERVER_TO_LOCAL.get(info.state)
        if local is not None:
            if record.state in (LocalState.STORED, LocalState.DOWNLOADING) \
                    and local in (LocalState.SUCCEEDED, LocalState.EXPIRED):
                local = record.state  # a local copy outlives the server's
            changes["state"] = local
            changes["submitted_confirmed"] = True
        updated = self.registry.update(record.client_job_id, **changes)
        if updated.submitted_confirmed and not record.submitted_confirmed:
            # Confirmed and fully uploaded: the server owns the inputs now.
            self.store.release_reference(record.client_job_id)
        return updated

    # Upload + submit -------------------------------------------------------
    def upload_and_submit(self, client_job_id: str, *,
                          cancelled: Callable[[], bool] = lambda: False,
                          progress: Callable[[int, int], None] = lambda d, t: None) \
            -> JobRecord:
        record = self.registry.get(client_job_id)
        if record is None:
            raise KeyError(client_job_id)
        if record.state in POSSIBLY_SUBMITTED:
            return self.reconcile(client_job_id)
        if record.state not in NOT_SUBMITTED:
            return record
        if not record.quote_accepted or not record.quote_id:
            raise ApprovalRequired("confirm the Cloud cost limit first")
        if record.retry_of:
            return self._submit_retry(record)   # the server reuses the package
        manifest = load_manifest(self.store, record.manifest_sha256)
        if not record.job_id:
            record = self.registry.update(client_job_id,
                                          state=LocalState.CREATE_REQUESTED)
            try:
                info = self.backend.create_job(
                    client_job_id=client_job_id, manifest=manifest,
                    idempotency_key=record.create_key,
                    display_name=dashboard_name(record.display_name))
            except wire.CloudUnconfirmed:
                self.registry.update(client_job_id, state=LocalState.UNCONFIRMED)
                return self.reconcile(client_job_id)
            except wire.CloudError:
                self.registry.update(client_job_id, state=LocalState.UPLOAD_PAUSED)
                raise
            record = self.registry.update(client_job_id, job_id=info.job_id,
                                          state=LocalState.UPLOADING,
                                          server_state=info.state,
                                          server_manifest_sha256=info.manifest_sha256)
        else:
            record = self.registry.update(client_job_id, state=LocalState.UPLOADING)
        try:
            record = self._upload_files(record, manifest, cancelled, progress)
        except UploadPaused:
            self.registry.update(client_job_id, state=LocalState.UPLOAD_PAUSED)
            raise
        return self._submit(record)

    def _upload_files(self, record, manifest, cancelled, progress) -> JobRecord:
        """One session per ``(role, name)``; resume at the server's offset.

        Up to ``UPLOAD_WORKERS`` files travel at once (each on its own
        kept-alive connection): Water Flow packages hold hundreds of files,
        and one file at a time spent most of the upload waiting for replies.
        """
        import threading
        from concurrent.futures import ThreadPoolExecutor, as_completed
        files = [item for item in manifest["files"]
                 if _file_key(item) not in record.uploaded]
        total = record.upload_bytes_total
        lock = threading.Lock()
        stop = threading.Event()
        counter = {"done": sum(item["size_bytes"] for item in manifest["files"]
                               if _file_key(item) in record.uploaded)}

        def stopped():
            return stop.is_set() or cancelled()

        def advance(count):
            with lock:
                counter["done"] += count
                done = counter["done"]
            progress(done, total)

        def send(item):
            # Each worker thread keeps its own connection for all its files;
            # it closes when the thread ends with the pool.
            self._upload_file(record.job_id, item, stopped, advance)
            self.registry.add_uploaded(record.client_job_id, _file_key(item), 0)

        workers = max(1, min(UPLOAD_WORKERS, len(files)))
        error = None
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="clothnext-upload") as pool:
            futures = [pool.submit(send, item) for item in files]
            for future in as_completed(futures):
                try:
                    future.result()
                except BaseException as exc:  # noqa: BLE001 - re-raised below
                    if error is None:
                        error = exc
                    stop.set()
        record = self.registry.update(record.client_job_id,
                                      upload_bytes_done=counter["done"])
        if error is not None:
            if isinstance(error, UploadPaused) or cancelled():
                raise UploadPaused("paused") from error
            raise error
        return record

    def _upload_file(self, job_id, item, cancelled, advance) -> None:
        sha, size = item["sha256"], item["size_bytes"]
        if cancelled():
            raise UploadPaused("paused")
        state = self._retry(lambda: self.backend.open_upload(
            job_id, item["role"], item["name"]), cancelled=cancelled)
        if state.confirmed:
            advance(size)    # reused: this account already owns the content
            return
        # Re-hash before sending (5.5): a damaged store never uploads.
        self.store.verify(sha, size=size)
        offset = state.received
        advance(offset)
        while offset < size:
            if cancelled():
                raise UploadPaused("paused")
            length = min(self._upload_block, size - offset)
            block = self.store.read_range(sha, offset, length)
            if length > SMALL_UPLOAD_BLOCK:
                # A proxy in front of the API may refuse bodies above its own
                # limit by closing the connection. One such refusal switches
                # this upload to small blocks right away (no backoff).
                try:
                    state = self.backend.upload_chunk(state.upload_id, offset, block)
                    advance(state.received - offset)
                    offset = state.received
                    continue
                except wire.CloudError as exc:
                    if exc.code == "network_unavailable":
                        _LOG.warning(
                            "Cloud upload block of %d bytes failed (%s, cause %r); "
                            "continuing with %d-byte blocks", length, exc.code,
                            exc.__cause__, SMALL_UPLOAD_BLOCK)
                        with self._block_lock:
                            self._upload_block = min(self._upload_block,
                                                     SMALL_UPLOAD_BLOCK)
                        continue
                    # Any other answer is handled by the regular path below.
                except wire.CloudUnconfirmed:
                    pass  # sent, no answer: the regular path repeats it safely
            try:
                state = self._retry(
                    lambda: self.backend.upload_chunk(state.upload_id, offset, block),
                    cancelled=cancelled)
            except wire.CloudError as exc:
                if exc.code == "offset_mismatch":
                    state = self._retry(lambda: self.backend.upload_status(
                        state.upload_id), cancelled=cancelled)
                elif exc.code == "upload_expired":
                    state = self._retry(lambda: self.backend.open_upload(
                        job_id, item["role"], item["name"]), cancelled=cancelled)
                else:
                    raise
            advance(state.received - offset)
            offset = state.received
        state = self._retry(lambda: self.backend.complete_upload(state.upload_id),
                            cancelled=cancelled)
        if not state.confirmed:
            raise wire.CloudError("hash_mismatch")

    def _submit(self, record: JobRecord) -> JobRecord:
        record = self.registry.update(record.client_job_id,
                                      state=LocalState.SUBMIT_REQUESTED)
        try:
            info = self.backend.submit(
                record.job_id, quote_id=record.quote_id,
                accepted_maximum=record.quote_maximum,
                idempotency_key=record.submit_key)
        except wire.CloudUnconfirmed:
            self.registry.update(record.client_job_id, state=LocalState.UNCONFIRMED)
            return self.reconcile(record.client_job_id)
        except wire.CloudError as exc:
            if exc.code in ("upload_incomplete", "hash_mismatch"):
                self.registry.update(record.client_job_id, uploaded=(),
                                     state=LocalState.UPLOAD_PAUSED)
            elif exc.code in ("invalid_quote", "quote_expired"):
                # Never re-approve silently: a new quote needs a new confirmation.
                self.registry.update(record.client_job_id, quote_accepted=False,
                                     state=LocalState.UPLOAD_PAUSED,
                                     error_code="quote_expired")
            else:
                # The server refused with a definite answer: nothing started.
                self.registry.update(record.client_job_id,
                                     state=LocalState.UPLOAD_PAUSED,
                                     error_code=exc.code)
            raise
        return self._adopt(record, info)

    # Reconciliation (E23.3/E23.4) ------------------------------------------
    def reconcile(self, client_job_id: str) -> JobRecord:
        record = self.registry.get(client_job_id)
        try:
            info = self.backend.find_job(client_job_id)
        except (wire.CloudUnconfirmed, wire.CloudError):
            # Still unknown: stay UNCONFIRMED ("Possibly started – checking").
            if record.state in (LocalState.SUBMIT_REQUESTED,
                                LocalState.CREATE_REQUESTED):
                return self.registry.update(client_job_id,
                                            state=LocalState.UNCONFIRMED)
            return record
        if info is None:
            # The server has no job for this id: nothing was created. The
            # next resume re-creates it with the SAME client_job_id.
            return self.registry.update(client_job_id, job_id="", uploaded=(),
                                        upload_bytes_done=0,
                                        state=LocalState.UPLOAD_PAUSED)
        if info.state == wire.UPLOADING:
            # Created but not submitted; resume re-submits with the same key.
            return self.registry.update(client_job_id, job_id=info.job_id,
                                        server_state=info.state,
                                        state=LocalState.UPLOAD_PAUSED)
        return self._adopt(record, info)

    def refresh(self, client_job_id: str) -> JobRecord:
        record = self.registry.get(client_job_id)
        if record.state in POSSIBLY_SUBMITTED or not record.job_id:
            return self.reconcile(client_job_id)
        info = self.backend.get_job(record.job_id)
        return self._adopt(record, info)

    def recover(self) -> tuple[JobRecord, ...]:
        """After a restart: settle unclear jobs first, refresh submitted ones.

        Uploads are not restarted automatically; the UI offers Resume /
        Discard for them.
        """
        settled = []
        for record in self.registry.nonterminal():
            try:
                if record.state in POSSIBLY_SUBMITTED or \
                        record.state is LocalState.CREATE_REQUESTED:
                    settled.append(self.reconcile(record.client_job_id))
                elif record.submitted_confirmed:
                    settled.append(self.refresh(record.client_job_id))
                else:
                    settled.append(record)
            except (wire.CloudUnconfirmed, wire.CloudError):
                settled.append(self.registry.get(record.client_job_id))
        return tuple(settled)

    # Cancel / discard ------------------------------------------------------
    def cancel(self, client_job_id: str) -> JobRecord:
        record = self.registry.get(client_job_id)
        if record.state in NOT_SUBMITTED:
            if record.job_id:
                try:
                    self.backend.cancel(record.job_id)
                except (wire.CloudUnconfirmed, wire.CloudError):
                    pass  # unsubmitted jobs cost nothing and expire (B6)
            self.store.release_reference(client_job_id)
            return self.registry.update(client_job_id, state=LocalState.DISCARDED)
        if record.state in POSSIBLY_SUBMITTED:
            record = self.reconcile(client_job_id)
            if not record.submitted_confirmed:
                return record
        info = self.backend.cancel(record.job_id)
        return self._adopt(record, info)

    # Download --------------------------------------------------------------
    def download(self, client_job_id: str, *,
                 cancelled: Callable[[], bool] = lambda: False,
                 progress: Callable[[int, int], None] = lambda d, t: None) -> JobRecord:
        record = self.registry.get(client_job_id)
        manifest = self._retry(lambda: self.backend.result_manifest(record.job_id))
        if manifest.snapshot_digest != record.snapshot_digest:
            raise ResultCorrupt("result belongs to a different package")
        expected = record.server_build_id or record.build_id
        if manifest.build_id != expected:
            raise ResultCorrupt(
                f"result was computed by {manifest.build_id}, expected {expected}")
        total = sum(item.size for item in manifest.files)
        free = self._free_bytes() if self._free_bytes else self.store.free_bytes()
        required = total + max(int(total * DOWNLOAD_RESERVE_RATIO), DOWNLOAD_RESERVE_MIN)
        if free < required:
            raise InsufficientDownloadSpace(required, free)
        self.registry.update(client_job_id, state=LocalState.DOWNLOADING)
        done = 0
        for item in manifest.files:
            def fetch(offset, length, _name=item.name):
                return self._retry(lambda: self.backend.download_result(
                    record.job_id, _name, offset, length), cancelled=cancelled)
            def advance(count):
                nonlocal done
                done += count
                progress(done, total)
            try:
                self.archive.download_file(client_job_id, item, fetch,
                                           progress=advance, cancelled=cancelled)
            except ResultCorrupt:
                # One clean re-download of the damaged file, then give up.
                self.archive.download_file(client_job_id, item, fetch,
                                           progress=advance, cancelled=cancelled)
        self._archive_snapshot(record)
        self.archive.store_manifest(client_job_id, manifest)
        self.archive.verify(client_job_id, manifest)
        return self.registry.update(client_job_id, state=LocalState.STORED,
                                    result_folder=client_job_id)

    def _archive_snapshot(self, record: JobRecord) -> None:
        """Keep the snapshot with the result; local copy first, else server."""
        raw = None
        if self.store.has(record.snapshot_digest):
            try:
                raw = self.store.verify(record.snapshot_digest).read_bytes()
            except (BlobMissing, BlobCorrupt):
                raw = None
        if raw is None:
            raw = self.backend.fetch_package_file(record.job_id, record.snapshot_digest)
        snapshot = load_snapshot(raw, expected_digest=record.snapshot_digest)
        self.archive.store_snapshot(record.client_job_id, snapshot.file_bytes)


def _file_key(item) -> str:
    return f"{item['role']}/{item['name']}"


def load_manifest(store: PackageStore, sha256: str, *, required: bool = True):
    import json
    try:
        return json.loads(store.verify(sha256).read_bytes())
    except (BlobMissing, BlobCorrupt):
        if required:
            raise
        return None
