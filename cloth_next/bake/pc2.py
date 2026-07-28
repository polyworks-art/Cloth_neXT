# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Transactional, bounded-memory POINTCACHE2 writing."""

from __future__ import annotations

import os
import shutil
import struct
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

PC2_MAGIC = b"POINTCACHE2\0"
PC2_VERSION = 1
PC2_HEADER_SIZE = 32
PC2_WRITER_VERSION = 3


class Pc2Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Pc2Header:
    vertex_count: int
    start_frame: float
    sample_rate: float
    frame_count: int


def _header_bytes(header: Pc2Header) -> bytes:
    return struct.pack("<12siiffi", PC2_MAGIC, PC2_VERSION,
                       header.vertex_count, header.start_frame,
                       header.sample_rate, header.frame_count)


class StreamingPc2Writer:
    """Write one frame at a time and publish only a complete cache."""

    def __init__(self, final_path: Path, *, vertex_count: int,
                 frame_count: int, start_frame: float = 0.0,
                 sample_rate: float = 1.0,
                 resume_path: Path | None = None) -> None:
        if vertex_count <= 0 or frame_count <= 0:
            raise Pc2Error("vertex_count and frame_count must be positive")
        self.final_path = Path(final_path)
        self.header = Pc2Header(vertex_count, float(start_frame),
                                float(sample_rate), frame_count)
        self.expected_size = (PC2_HEADER_SIZE
                              + frame_count * vertex_count * 12)
        self.frames_written = 0
        self.bytes_written = 0
        self.flush_seconds = 0.0
        self.validation_seconds = 0.0
        self._finished = False
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        self.temporary_path = (
            Path(resume_path) if resume_path is not None
            else self.final_path.with_name(
                f".{self.final_path.name}.{uuid.uuid4().hex}.tmp"))
        try:
            if self.temporary_path.exists():
                self._resume_existing()
            else:
                self.temporary_path.parent.mkdir(parents=True, exist_ok=True)
                self._stream = self.temporary_path.open("xb")
                written = self._stream.write(_header_bytes(self.header))
                if written != PC2_HEADER_SIZE:
                    raise OSError("short PC2 header write")
                self.bytes_written = written
        except Exception:
            self.abort()
            raise

    def _resume_existing(self) -> None:
        stream = self.temporary_path.open("r+b")
        raw = stream.read(PC2_HEADER_SIZE)
        if raw != _header_bytes(self.header):
            stream.close()
            raise Pc2Error("partial PC2 header does not match this Bake")
        stream.seek(0, 2)
        size = stream.tell()
        frame_bytes = self.header.vertex_count * 12
        payload = size - PC2_HEADER_SIZE
        if (payload < 0 or payload % frame_bytes
                or size > self.expected_size):
            stream.close()
            raise Pc2Error("partial PC2 has an invalid frame boundary")
        self.frames_written = payload // frame_bytes
        self.bytes_written = size
        self._stream = stream

    def write_frame(self, positions) -> None:
        if self._finished:
            raise Pc2Error("writer is already finalized or aborted")
        if self.frames_written >= self.header.frame_count:
            raise Pc2Error("more frames than declared were written")
        array = np.asarray(positions)
        expected_shape = (self.header.vertex_count, 3)
        if array.shape != expected_shape:
            raise Pc2Error(f"frame shape {array.shape} does not match "
                           f"{expected_shape} (constant topology)")
        if not np.isfinite(array).all():
            raise Pc2Error("frame contains a non-finite position")
        # astype(copy=False) preserves an already contiguous <f4 input.  A
        # single frame-sized copy is made only when dtype/layout requires it.
        array = np.ascontiguousarray(array, dtype=np.dtype("<f4"))
        payload = memoryview(array).cast("B")
        expected = self.header.vertex_count * 12
        if payload.nbytes != expected:
            raise Pc2Error("encoded PC2 frame has the wrong byte count")
        written = self._stream.write(payload)
        if written != expected:
            raise OSError(f"short PC2 frame write: {written}/{expected}")
        self.frames_written += 1
        self.bytes_written += written

    def finalize(self) -> Pc2Header:
        if self._finished:
            raise Pc2Error("writer is already finalized or aborted")
        backup = self.final_path.with_name(
            f".{self.final_path.name}.{uuid.uuid4().hex}.bak")
        try:
            if self.frames_written != self.header.frame_count:
                raise Pc2Error(f"wrote {self.frames_written} frames, expected "
                               f"{self.header.frame_count}")
            if self.bytes_written != self.expected_size:
                raise Pc2Error("tracked PC2 size does not match expected size")
            if self._stream.tell() != self.expected_size:
                raise Pc2Error("temporary PC2 stream position is invalid")
            step = time.monotonic()
            self._stream.flush()
            actual = os.fstat(self._stream.fileno()).st_size
            if actual != self.expected_size:
                raise Pc2Error(f"temporary PC2 has {actual} bytes, expected "
                               f"{self.expected_size}")
            os.fsync(self._stream.fileno())
            self.flush_seconds = time.monotonic() - step
            self._stream.close()
            if self.final_path.exists():
                os.link(self.final_path, backup)
            os.replace(self.temporary_path, self.final_path)
            step = time.monotonic()
            verified = read_header(self.final_path)
            self.validation_seconds = time.monotonic() - step
            if verified != self.header:
                raise Pc2Error("published PC2 header validation failed")
            backup.unlink(missing_ok=True)
            self._finished = True
            return verified
        except Exception:
            if backup.exists():
                try:
                    os.replace(backup, self.final_path)
                except OSError:
                    pass
            elif self.final_path.exists() and not self.temporary_path.exists():
                self.final_path.unlink(missing_ok=True)
            self.abort()
            raise

    def preserve(self) -> Path:
        """Flush a frame-aligned partial cache without publishing it."""
        if self._finished:
            raise Pc2Error("writer is already finalized or aborted")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        self._finished = True
        return self.temporary_path

    def publish_partial_preview(self) -> Pc2Header:
        """Preserve resume data and atomically publish its playable prefix.

        The recovery file keeps the original full-range header so a later
        writer can append to it. A separate final PC2 receives a shortened
        header and exactly the complete frames written so far, making the
        cancelled Bake immediately usable by Blender without sacrificing the
        resumable stream.
        """
        if self._finished:
            raise Pc2Error("writer is already finalized or aborted")
        if self.frames_written <= 0:
            raise Pc2Error("partial PC2 contains no complete frames")
        partial_path = self.preserve()
        preview_header = Pc2Header(
            self.header.vertex_count, self.header.start_frame,
            self.header.sample_rate, self.frames_written)
        expected_size = (PC2_HEADER_SIZE + self.frames_written
                         * self.header.vertex_count * 12)
        temporary = self.final_path.with_name(
            f".{self.final_path.name}.{uuid.uuid4().hex}.partial.tmp")
        backup = self.final_path.with_name(
            f".{self.final_path.name}.{uuid.uuid4().hex}.bak")
        try:
            with partial_path.open("rb") as source, temporary.open("xb") as target:
                raw = source.read(PC2_HEADER_SIZE)
                if raw != _header_bytes(self.header):
                    raise Pc2Error("partial PC2 header changed before preview")
                target.write(_header_bytes(preview_header))
                shutil.copyfileobj(source, target, length=4 * 1024 * 1024)
                target.flush()
                if target.tell() != expected_size:
                    raise Pc2Error("partial PC2 preview ended off frame boundary")
                os.fsync(target.fileno())
            if self.final_path.exists():
                os.link(self.final_path, backup)
            os.replace(temporary, self.final_path)
            verified = read_header(self.final_path)
            if verified != preview_header:
                raise Pc2Error("partial PC2 preview validation failed")
            backup.unlink(missing_ok=True)
            return verified
        except Exception:
            temporary.unlink(missing_ok=True)
            if backup.exists():
                try:
                    os.replace(backup, self.final_path)
                except OSError:
                    pass
            raise

    def abort(self) -> None:
        if getattr(self, "_finished", False):
            return
        stream = getattr(self, "_stream", None)
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass
        temporary = getattr(self, "temporary_path", None)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        self._finished = True

    def __enter__(self) -> "StreamingPc2Writer":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None or not self._finished:
            self.abort()


def write_pc2(path: Path, frames: Sequence[Sequence[Sequence[float]]], *,
              start_frame: float = 0.0, sample_rate: float = 1.0) -> Pc2Header:
    """Compatibility wrapper; production callers use StreamingPc2Writer."""
    if not frames:
        raise Pc2Error("at least one frame is required")
    vertex_count = len(frames[0])
    writer = StreamingPc2Writer(path, vertex_count=vertex_count,
                                frame_count=len(frames),
                                start_frame=start_frame,
                                sample_rate=sample_rate)
    try:
        for frame in frames:
            writer.write_frame(frame)
        return writer.finalize()
    except Exception:
        writer.abort()
        raise


def read_header(path: Path) -> Pc2Header:
    with path.open("rb") as stream:
        raw = stream.read(PC2_HEADER_SIZE)
        stream.seek(0, 2)
        actual_size = stream.tell()
    if len(raw) != PC2_HEADER_SIZE or raw[:12] != PC2_MAGIC:
        raise Pc2Error(f"{path.name} is not a POINTCACHE2 file")
    version, vertex_count = struct.unpack_from("<ii", raw, 12)
    start_frame, sample_rate = struct.unpack_from("<ff", raw, 20)
    frame_count = struct.unpack_from("<i", raw, 28)[0]
    if version != PC2_VERSION:
        raise Pc2Error(f"unsupported PC2 version {version}")
    if vertex_count <= 0 or frame_count <= 0:
        raise Pc2Error("PC2 header declares no data")
    expected = PC2_HEADER_SIZE + frame_count * vertex_count * 12
    if actual_size != expected:
        raise Pc2Error(f"PC2 size mismatch: file has {actual_size} bytes, "
                       f"header implies {expected}")
    return Pc2Header(vertex_count, start_frame, sample_rate, frame_count)


def partial_frame_count(path: Path, expected: Pc2Header) -> int:
    """Validate a resumable partial file and return complete frame count."""
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(PC2_HEADER_SIZE)
        stream.seek(0, 2)
        size = stream.tell()
    if raw != _header_bytes(expected):
        raise Pc2Error("partial PC2 header does not match")
    frame_bytes = expected.vertex_count * 12
    payload = size - PC2_HEADER_SIZE
    if payload < 0 or payload % frame_bytes:
        raise Pc2Error("partial PC2 ends inside a frame")
    frames = payload // frame_bytes
    if frames > expected.frame_count:
        raise Pc2Error("partial PC2 contains too many frames")
    return frames


def iter_frames(path: Path):
    """Yield validated PC2 frames one at a time as ``(N, 3)`` float arrays."""
    header = read_header(path)
    frame_bytes = header.vertex_count * 12
    with path.open("rb") as stream:
        stream.seek(PC2_HEADER_SIZE)
        for index in range(header.frame_count):
            raw = stream.read(frame_bytes)
            if len(raw) != frame_bytes:
                raise Pc2Error(f"short PC2 frame {index}")
            yield np.frombuffer(raw, dtype="<f4").reshape(
                header.vertex_count, 3).copy()
