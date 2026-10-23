# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Archive of downloaded raw Cloud results, one folder per job (E17, E20).

The archive lives in the account root (``cloud-results/<client_job_id>``),
not in any Cloth NeXt cache folder and never under the ``cn_test_cloth_``
prefix, so Clear Result and local Bake cleanup cannot reach it. It is the
source for every later activation, also after the server copy expired.
Only ``delete_job`` removes a folder; callers confirm with the artist first.

Raw frames arrive as blocks of consecutive ``vert_<N>.bin`` payloads. Every
file is downloaded to ``<name>.partial`` with resumable offsets and moved
into place only after its SHA-256 matches the result manifest.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable, Iterator

from ..core.safe_delete import delete_owned
from ..polyworks import storage
from .transport import ResultFile, ResultManifest

RESULTS_DIRECTORY = "cloud-results"
MANIFEST_NAME = "result-manifest.json"
SNAPSHOT_FILE = "import-snapshot"
MAP_NAME = "vertex-map.bin"
SURFACE_MAP_NAME = "surface-map.bin"
_BLOCK = re.compile(r"frames-(\d{6})-(\d{6})\.bin")
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]{1,96}")
_JOB = re.compile(r"[A-Za-z0-9_-]{8,128}")
DOWNLOAD_CHUNK = 4 * 1024 * 1024


class ResultError(RuntimeError):
    code = "result_corrupt"


class ResultCorrupt(ResultError):
    code = "result_corrupt"


def default_root(environ=None) -> Path:
    return storage.account_root(environ) / RESULTS_DIRECTORY


def block_name(first: int, last: int) -> str:
    return f"frames-{first:06d}-{last:06d}.bin"


def parse_block_name(name: str) -> tuple[int, int] | None:
    match = _BLOCK.fullmatch(name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class ResultArchive:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def folder(self, client_job_id: str) -> Path:
        if not _JOB.fullmatch(client_job_id or ""):
            raise ResultError("invalid client job id")
        return self.root / client_job_id

    def file_path(self, client_job_id: str, name: str) -> Path:
        if not _SAFE_NAME.fullmatch(name) or name.startswith("."):
            raise ResultCorrupt(f"unsafe result file name {name!r}")
        return self.folder(client_job_id) / name

    # Download ----------------------------------------------------------------
    def download_file(self, client_job_id: str, item: ResultFile,
                      fetch: Callable[[int, int], bytes], *,
                      progress: Callable[[int], None] = lambda n: None,
                      cancelled: Callable[[], bool] = lambda: False) -> Path:
        """Resumable download of one result file; verified before use."""
        target = self.file_path(client_job_id, item.name)
        if target.is_file() and target.stat().st_size == item.size \
                and _sha256_file(target) == item.sha256:
            return target
        storage.ensure_private_directory(target.parent)
        partial = target.with_name(target.name + ".partial")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > item.size:
            partial.unlink()
            offset = 0
        with partial.open("ab") as stream:
            while offset < item.size:
                if cancelled():
                    raise InterruptedError("download paused")
                length = min(DOWNLOAD_CHUNK, item.size - offset)
                block = fetch(offset, length)
                if not block or len(block) > length:
                    raise ResultCorrupt("result block has the wrong size")
                stream.write(block)
                offset += len(block)
                progress(len(block))
            stream.flush()
            os.fsync(stream.fileno())
        if _sha256_file(partial) != item.sha256:
            partial.unlink(missing_ok=True)
            raise ResultCorrupt(f"result file {item.name} failed its hash check")
        os.replace(partial, target)
        return target

    def store_manifest(self, client_job_id: str, manifest: ResultManifest) -> None:
        document = asdict(manifest)
        document["files"] = [asdict(item) for item in manifest.files]
        storage.atomic_write(self.file_path(client_job_id, MANIFEST_NAME),
                             json.dumps(document, sort_keys=True, indent=1))

    def store_snapshot(self, client_job_id: str, file_bytes: bytes) -> None:
        storage.atomic_write(self.file_path(client_job_id, SNAPSHOT_FILE), file_bytes)

    # Reading -----------------------------------------------------------------
    def load_manifest(self, client_job_id: str) -> ResultManifest:
        path = self.file_path(client_job_id, MANIFEST_NAME)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            files = tuple(ResultFile(**item) for item in document.pop("files"))
            return ResultManifest(files=files, **document)
        except (OSError, ValueError, TypeError) as exc:
            raise ResultCorrupt("stored result manifest is unreadable") from exc

    def load_snapshot_bytes(self, client_job_id: str) -> bytes:
        return self.file_path(client_job_id, SNAPSHOT_FILE).read_bytes()

    def verify(self, client_job_id: str, manifest: ResultManifest) -> None:
        for item in manifest.files:
            path = self.file_path(client_job_id, item.name)
            if not path.is_file() or path.stat().st_size != item.size \
                    or _sha256_file(path) != item.sha256:
                raise ResultCorrupt(f"stored result file {item.name} is damaged")

    def is_complete(self, client_job_id: str) -> bool:
        try:
            manifest = self.load_manifest(client_job_id)
            self.verify(client_job_id, manifest)
            self.load_snapshot_bytes(client_job_id)
            return True
        except (ResultError, OSError):
            return False

    def iter_frames(self, client_job_id: str, manifest: ResultManifest)             -> Iterator[tuple[int, bytes]]:
        """Yield ``(solver_frame, raw vert bytes)`` for 1..frame_count.

        Every solver frame has the same size (all solver vertices, float32
        XYZ), so a block of N frames is N equal slices.
        """
        frame_bytes = None
        blocks = sorted((parse_block_name(item.name), item)
                        for item in manifest.files
                        if parse_block_name(item.name) is not None)
        expected = 1
        for (first, last), item in blocks:
            if first != expected or last < first:
                raise ResultCorrupt("result frame blocks are not contiguous")
            raw = self.file_path(client_job_id, item.name).read_bytes()
            count = last - first + 1
            if not raw or len(raw) % (count * 12):
                raise ResultCorrupt(f"frame block {item.name} has the wrong size")
            size = len(raw) // count
            if frame_bytes is None:
                frame_bytes = size
            if size != frame_bytes:
                raise ResultCorrupt("result frames differ in size")
            for index, frame in enumerate(range(first, last + 1)):
                yield frame, raw[index * frame_bytes:(index + 1) * frame_bytes]
            expected = last + 1
        if expected - 1 != manifest.frame_count:
            raise ResultCorrupt("result frame count does not match its blocks")

    def size_bytes(self, client_job_id: str) -> int:
        folder = self.folder(client_job_id)
        return sum(path.stat().st_size for path in folder.rglob("*")
                   if path.is_file()) if folder.is_dir() else 0

    def delete_job(self, client_job_id: str, *, confirmed: bool) -> None:
        """Remove one downloaded result; only after the artist confirmed."""
        if not confirmed:
            raise ResultError("deleting a downloaded Cloud result needs confirmation")
        folder = self.folder(client_job_id)
        if folder.exists():
            delete_owned(folder, root=self.root, ownership_authenticated=True,
                         lifecycle_stage="CLOUD_RESULT_DELETE",
                         artifact_type="cloud_result_archive", recursive=True)
