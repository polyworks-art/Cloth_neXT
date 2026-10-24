# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local Cloud job registry: a cache and recovery log, never the authority.

One JSON document per OS user (``cloud-jobs.json`` in the account root),
replaced atomically under a cross-process lock. It holds only non-secret
data: ids, idempotency keys, the last known state, upload progress, package
hashes, the snapshot digest and local folder names. No tokens, no URLs.

Intent first (E23): before a request that can create a job or a charge, the
record moves to ``CREATE_REQUESTED`` / ``SUBMIT_REQUESTED`` and is flushed;
only the server's answer moves it on. A crash in between leaves a visibly
unclear record that ``jobs.reconcile`` resolves with the server.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import json
import os
from pathlib import Path
import threading
import time
from typing import Iterator

from ..polyworks import storage
from . import privacy

REGISTRY_FILE = "cloud-jobs.json"
LOCK_FILE = "cloud-jobs.lock"
REGISTRY_VERSION = 1


class LocalState(str, Enum):
    PACKAGED = "PACKAGED"                  # package stored, nothing sent
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # quote shown; the artist must confirm
    CREATE_REQUESTED = "CREATE_REQUESTED"  # intent: create_job in flight
    UPLOADING = "UPLOADING"
    UPLOAD_PAUSED = "UPLOAD_PAUSED"        # resumable; nothing submitted
    SUBMIT_REQUESTED = "SUBMIT_REQUESTED"  # intent: submit in flight
    UNCONFIRMED = "UNCONFIRMED"            # outcome unknown; must reconcile
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"                # ready on server
    DOWNLOADING = "DOWNLOADING"
    STORED = "STORED"                      # raw result archived locally
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    STOPPED_AT_LIMIT = "STOPPED_AT_LIMIT"
    EXPIRED = "EXPIRED"
    DISCARDED = "DISCARDED"                # never submitted, dropped locally


# A submitted job may have cost money: these never read as "not sent".
POSSIBLY_SUBMITTED = frozenset({LocalState.SUBMIT_REQUESTED,
                                LocalState.UNCONFIRMED})
NOT_SUBMITTED = frozenset({LocalState.PACKAGED, LocalState.AWAITING_APPROVAL,
                           LocalState.CREATE_REQUESTED,
                           LocalState.UPLOADING, LocalState.UPLOAD_PAUSED})
TERMINAL = frozenset({LocalState.STORED, LocalState.FAILED,
                      LocalState.CANCELLED, LocalState.EXPIRED,
                      LocalState.DISCARDED})


@dataclass(frozen=True, slots=True)
class JobRecord:
    client_job_id: str
    state: LocalState
    display_name: str
    engine: str
    build_id: str
    manifest_sha256: str
    snapshot_digest: str
    package_hashes: tuple[str, ...]
    created_at: float
    updated_at: float
    job_id: str = ""
    create_key: str = ""
    submit_key: str = ""
    cancel_key: str = ""
    uploaded: tuple[str, ...] = ()          # server-confirmed file hashes
    upload_bytes_done: int = 0
    upload_bytes_total: int = 0
    server_state: str = ""
    progress_frames: int | None = None
    total_frames: int | None = None
    queue_position: int | None = None
    eta_seconds: float | None = None
    expires_at: str | None = None
    error_code: str = ""
    result_folder: str = ""                 # folder name below the results root
    blend_identity: str = ""                # opaque per-.blend key (T10)
    object_uuids: tuple[str, ...] = ()      # export UUIDs: finds the job's .blend objects
    submitted_confirmed: bool = False
    server_manifest_sha256: str = ""        # authoritative, computed by the server
    server_build_id: str = ""               # server build mapped to this desktop build
    quote_id: str = ""
    quote_maximum: float | None = None
    quote_unit: str = ""
    quote_expires_at: str = ""
    quote_billing_mode: str = ""
    quote_max_seconds: float | None = None  # server's maximum execution time
    quote_accepted: bool = False            # explicit artist confirmation (E10)
    final_cost: str | None = None           # server's final cost, once known
    quote_gpu: str = ""                     # server-selected GPU of the quote
    quote_usage_percent: int | None = None  # 290: one credit lasts 2.9 s
    gpu_type_id: str = ""                   # GPU the job ran / runs on
    billing_reserved: int | None = None
    billing_finalized: bool = False
    elapsed_ms: int | None = None           # server accounting duration
    retryable: bool = False                 # server allows POST /retry
    retried_as: str = ""                    # local client_job_id of the retry
    retry_of: str = ""                      # local client_job_id this retries

    def to_json(self) -> dict:
        value = asdict(self)
        value["state"] = self.state.value
        value["package_hashes"] = list(self.package_hashes)
        value["uploaded"] = list(self.uploaded)
        value["object_uuids"] = list(self.object_uuids)
        return value

    @classmethod
    def from_json(cls, value: dict) -> "JobRecord":
        data = dict(value)
        data["state"] = LocalState(data["state"])
        data["package_hashes"] = tuple(data.get("package_hashes", ()))
        data["uploaded"] = tuple(data.get("uploaded", ()))
        data["object_uuids"] = tuple(data.get("object_uuids", ()))
        known = set(cls.__dataclass_fields__)
        return cls(**{key: item for key, item in data.items() if key in known})


class RegistryError(RuntimeError):
    pass


_thread_lock = threading.RLock()


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    # Threads of this process queue here first: msvcrt.LK_LOCK waits a full
    # second between attempts, which parallel uploads would hit constantly.
    with _thread_lock:
        yield from _file_locked(root)


def _file_locked(root: Path) -> Iterator[None]:
    storage.ensure_private_directory(root)
    with open(root / LOCK_FILE, "a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class JobRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else storage.account_root()

    @property
    def path(self) -> Path:
        return self.root / REGISTRY_FILE

    def _read(self) -> dict[str, JobRecord]:
        if not self.path.exists():
            return {}
        text = storage.read_trusted_text(self.path, 16 * 1024 * 1024)
        if text is None:
            raise RegistryError("the Cloud job registry is unreadable")
        try:
            document = json.loads(text)
            if document.get("version") != REGISTRY_VERSION:
                raise RegistryError("unsupported Cloud job registry version")
            return {item["client_job_id"]: JobRecord.from_json(item)
                    for item in document.get("jobs", ())}
        except (ValueError, KeyError, TypeError) as exc:
            raise RegistryError("the Cloud job registry is damaged") from exc

    def _write(self, records: dict[str, JobRecord]) -> None:
        document = {"version": REGISTRY_VERSION,
                    "jobs": [records[key].to_json() for key in sorted(records)]}
        privacy.require_clean(document, label="Cloud job registry")
        storage.atomic_write(self.path, json.dumps(document, sort_keys=True,
                                                   indent=1))

    def all(self) -> tuple[JobRecord, ...]:
        with _locked(self.root):
            records = self._read()
        return tuple(sorted(records.values(), key=lambda item: item.created_at))

    def get(self, client_job_id: str) -> JobRecord | None:
        with _locked(self.root):
            return self._read().get(client_job_id)

    def add(self, record: JobRecord) -> JobRecord:
        with _locked(self.root):
            records = self._read()
            if record.client_job_id in records:
                raise RegistryError("Cloud job already registered")
            records[record.client_job_id] = record
            self._write(records)
        return record

    def update(self, client_job_id: str, **changes) -> JobRecord:
        """Atomically apply ``changes``; flushed before this returns."""
        with _locked(self.root):
            records = self._read()
            current = records.get(client_job_id)
            if current is None:
                raise RegistryError("unknown Cloud job")
            changes.setdefault("updated_at", time.time())
            updated = replace(current, **changes)
            records[client_job_id] = updated
            self._write(records)
        return updated

    def add_uploaded(self, client_job_id: str, key: str, size: int) -> JobRecord:
        """Record one confirmed file; safe for parallel uploads of one job."""
        with _locked(self.root):
            records = self._read()
            current = records.get(client_job_id)
            if current is None:
                raise RegistryError("unknown Cloud job")
            if key in current.uploaded:
                return current
            updated = replace(current, uploaded=tuple(sorted({*current.uploaded, key})),
                              upload_bytes_done=current.upload_bytes_done + size,
                              updated_at=time.time())
            records[client_job_id] = updated
            self._write(records)
        return updated

    def nonterminal(self) -> tuple[JobRecord, ...]:
        """Jobs to resume or reconcile; unclear ones first (E23.4)."""
        order = {LocalState.UNCONFIRMED: 0, LocalState.SUBMIT_REQUESTED: 1,
                 LocalState.CREATE_REQUESTED: 2}
        return tuple(sorted(
            (item for item in self.all() if item.state not in TERMINAL),
            key=lambda item: (order.get(item.state, 9), item.created_at)))


def new_record(*, client_job_id: str, display_name: str, solver: dict,
               manifest_sha256: str, snapshot_digest: str,
               package_hashes, upload_bytes_total: int,
               blend_identity: str = "", object_uuids=()) -> JobRecord:
    now = time.time()
    return JobRecord(
        client_job_id=client_job_id, state=LocalState.PACKAGED,
        display_name=display_name, engine=solver["engine"],
        build_id=solver["build_id"], manifest_sha256=manifest_sha256,
        snapshot_digest=snapshot_digest,
        package_hashes=tuple(sorted(package_hashes)),
        created_at=now, updated_at=now,
        # Stable keys exist before the first network call (E23.1).
        create_key=client_job_id, submit_key=f"{client_job_id}:submit",
        cancel_key=f"{client_job_id}:cancel",
        upload_bytes_total=upload_bytes_total, blend_identity=blend_identity,
        object_uuids=tuple(sorted(object_uuids)))
