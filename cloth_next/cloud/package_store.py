# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable, content-addressed store for Cloud package files (E24, T14).

Blender's temp folder disappears on quit and the export cache replaces and
cleans its files, so neither can back a resumable upload. Files here are
named by the SHA-256 of their bytes, written once (temporary file, fsync,
hash check, ``os.replace``), made read-only and never changed again.

Layout below ``root``::

    blobs/<aa>/<sha256>     package files (read-only)
    refs/<client_job_id>.json   hashes an open job still needs
    tmp/                    in-flight copies (safe to clean)

A blob is only removed by ``collect_garbage``: when no reference names it
and it is older than the grace period. Local Bakes, Clear Result and export
cache cleanup never touch this root.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid
from typing import Iterable

from ..core.safe_delete import delete_owned
from ..polyworks import storage

CHUNK = 1024 * 1024
STORE_DIRECTORY = "cloud-packages"
DEFAULT_GRACE_SECONDS = 7 * 24 * 3600
_SHA256 = re.compile(r"[0-9a-f]{64}")
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{8,128}")


class PackageStoreError(RuntimeError):
    """Base class; ``code`` maps to a CNX Cloud error code."""

    code = "package_store"


class BlobMissing(PackageStoreError):
    code = "local_package_missing"


class BlobCorrupt(PackageStoreError):
    code = "local_package_corrupt"


class InsufficientSpace(PackageStoreError):
    code = "insufficient_disk_space"

    def __init__(self, required: int, available: int) -> None:
        super().__init__(
            f"{required - available} bytes more free space are needed")
        self.required = required
        self.available = available


@dataclass(frozen=True, slots=True)
class StoredBlob:
    sha256: str
    size: int


def default_root(environ=None) -> Path:
    return storage.account_root(environ) / STORE_DIRECTORY


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":  # directories cannot be opened for fsync on Windows
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise PackageStoreError("not a lowercase SHA-256 digest")
    return value


class PackageStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    # Paths -------------------------------------------------------------------
    def blob_path(self, sha256: str) -> Path:
        digest = require_sha256(sha256)
        return self.root / "blobs" / digest[:2] / digest

    def _ref_path(self, client_job_id: str) -> Path:
        if not _JOB_ID.fullmatch(client_job_id or ""):
            raise PackageStoreError("invalid client job id")
        return self.root / "refs" / f"{client_job_id}.json"

    def _tmp(self) -> Path:
        directory = self.root / "tmp"
        storage.ensure_private_directory(directory)
        return directory / f"{uuid.uuid4().hex}.tmp"

    # Space -------------------------------------------------------------------
    def free_bytes(self) -> int:
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return shutil.disk_usage(probe).free

    def require_space(self, required_bytes: int, *, reserve_bytes: int = 0) -> None:
        required = int(required_bytes) + int(reserve_bytes)
        available = self.free_bytes()
        if available < required:
            raise InsufficientSpace(required, available)

    # Writing -----------------------------------------------------------------
    def _publish(self, temporary: Path, digest: str, size: int,
                 expected_sha256: str | None) -> StoredBlob:
        if expected_sha256 is not None and digest != expected_sha256:
            temporary.unlink(missing_ok=True)
            raise BlobCorrupt("package source changed while it was stored")
        target = self.blob_path(digest)
        if target.is_file() and target.stat().st_size == size:
            # Identical content is already immutable here (shared by jobs).
            temporary.unlink(missing_ok=True)
            return StoredBlob(digest, size)
        storage.ensure_private_directory(target.parent)
        os.chmod(temporary, stat.S_IREAD)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
        return StoredBlob(digest, size)

    def put_file(self, source: Path, *,
                 expected_sha256: str | None = None) -> StoredBlob:
        """Atomically copy ``source`` into the store while hashing it."""
        temporary = self._tmp()
        digest = hashlib.sha256()
        size = 0
        try:
            with Path(source).open("rb") as reader, temporary.open("xb") as writer:
                while block := reader.read(CHUNK):
                    digest.update(block)
                    writer.write(block)
                    size += len(block)
                writer.flush()
                os.fsync(writer.fileno())
            return self._publish(temporary, digest.hexdigest(), size,
                                 expected_sha256)
        except BaseException:
            if temporary.exists():
                os.chmod(temporary, stat.S_IWRITE | stat.S_IREAD)
                temporary.unlink(missing_ok=True)
            raise

    def put_bytes(self, data: bytes | memoryview, *,
                  expected_sha256: str | None = None) -> StoredBlob:
        temporary = self._tmp()
        raw = bytes(data)
        try:
            with temporary.open("xb") as writer:
                writer.write(raw)
                writer.flush()
                os.fsync(writer.fileno())
            return self._publish(temporary, hashlib.sha256(raw).hexdigest(),
                                 len(raw), expected_sha256)
        except BaseException:
            if temporary.exists():
                os.chmod(temporary, stat.S_IWRITE | stat.S_IREAD)
                temporary.unlink(missing_ok=True)
            raise

    # Reading -----------------------------------------------------------------
    def verify(self, sha256: str, *, size: int | None = None) -> Path:
        """Re-hash the stored file; never hand out unverified bytes."""
        path = self.blob_path(sha256)
        if not path.is_file():
            raise BlobMissing(f"package file {sha256[:12]} is missing")
        if size is not None and path.stat().st_size != size:
            raise BlobCorrupt(f"package file {sha256[:12]} changed size")
        digest = hashlib.sha256()
        with path.open("rb") as reader:
            while block := reader.read(CHUNK):
                digest.update(block)
        if digest.hexdigest() != sha256:
            raise BlobCorrupt(f"package file {sha256[:12]} is corrupt")
        return path

    def read_range(self, sha256: str, offset: int, length: int) -> bytes:
        with self.blob_path(sha256).open("rb") as reader:
            reader.seek(offset)
            return reader.read(length)

    def has(self, sha256: str) -> bool:
        return self.blob_path(sha256).is_file()

    # References --------------------------------------------------------------
    def add_reference(self, client_job_id: str, hashes: Iterable[str]) -> None:
        path = self._ref_path(client_job_id)
        values = sorted({require_sha256(value) for value in hashes})
        storage.atomic_write(path, json.dumps(
            {"version": 1, "client_job_id": client_job_id, "blobs": values},
            sort_keys=True))

    def release_reference(self, client_job_id: str) -> None:
        path = self._ref_path(client_job_id)
        if path.exists():
            delete_owned(path, root=self.root / "refs",
                         ownership_authenticated=True,
                         lifecycle_stage="CLOUD_PACKAGE_RELEASE",
                         artifact_type="cloud_package_reference",
                         tombstone=False)

    def referenced(self) -> set[str]:
        result: set[str] = set()
        directory = self.root / "refs"
        if not directory.is_dir():
            return result
        for path in directory.glob("*.json"):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                result.update(require_sha256(value)
                              for value in document.get("blobs", ()))
            except (OSError, ValueError, PackageStoreError):
                # An unreadable reference protects everything: fail closed.
                return {"*"}
        return result

    def collect_garbage(self, *, grace_seconds: float = DEFAULT_GRACE_SECONDS,
                        now: float | None = None) -> list[str]:
        """Remove unreferenced blobs older than ``grace_seconds``."""
        keep = self.referenced()
        if "*" in keep:
            return []
        now = time.time() if now is None else now
        removed = []
        blobs = self.root / "blobs"
        if not blobs.is_dir():
            return removed
        for path in sorted(blobs.glob("??/*")):
            name = path.name
            if not _SHA256.fullmatch(name) or name in keep:
                continue
            if now - path.stat().st_mtime < grace_seconds:
                continue
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            delete_owned(path, root=blobs, ownership_authenticated=True,
                         lifecycle_stage="CLOUD_PACKAGE_GC",
                         artifact_type="cloud_package_blob", tombstone=False)
            removed.append(name)
        return removed
