# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validated result sources for the existing solver session.

Owned processes may read only the project tree whose data root Cloth NeXt
created.  External servers remain transport-only and never expose a local
path.
"""

from __future__ import annotations

import mmap
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

import numpy as np

from ..ppf import results, wire
from ..ppf.transport import TransportConfig


class UnsafeResultPath(ValueError):
    pass


@dataclass(slots=True)
class FrameData:
    positions: np.ndarray | None


def owned_project_root(work_directory: Path, project_name: str) -> Path:
    """Derive, never discover, the private project root."""
    if (not project_name or project_name in {".", ".."}
            or Path(project_name).is_absolute()
            or "/" in project_name or "\\" in project_name):
        raise UnsafeResultPath("invalid owned solver project name")
    data_root = (Path(work_directory) / "server-data").resolve()
    root = (data_root / project_name).resolve()
    try:
        root.relative_to(data_root)
    except ValueError as exc:
        raise UnsafeResultPath("owned solver project escapes its data root") from exc
    return root


def contained_result_path(root: Path, relative: str, expected_name: str) -> Path:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or rel.name != expected_name:
        raise UnsafeResultPath("unsafe solver result relative path")
    resolved_root = Path(root).resolve()
    candidate = resolved_root.joinpath(*rel.parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise UnsafeResultPath("solver result path escapes its project root") from exc
    if candidate.name != expected_name:
        raise UnsafeResultPath("unexpected solver result filename")
    return candidate


class TcpResultSource:
    kind = "tcp"

    def __init__(self, address: wire.ServerAddress, transport: TransportConfig,
                 project_name: str) -> None:
        self.address, self.transport = address, transport
        self.project_name = project_name
        self.bytes_transferred = 0
        self.bytes_read = 0
        self.connections = 0

    def output_map_blob(self) -> bytes:
        blob = wire.data_receive(self.address, self.transport,
                                 project_name=self.project_name,
                                 path=results.MAP_PATH)
        self.bytes_transferred += len(blob)
        self.connections += 1
        return blob

    @contextmanager
    def frame_positions(self, frame: int, total_vertices: int,
                        check_cancel: Callable[[], None]) -> Iterator[FrameData]:
        del check_cancel
        blob = wire.data_receive(self.address, self.transport,
                                 project_name=self.project_name,
                                 path=results.frame_file_path(frame))
        self.bytes_transferred += len(blob)
        self.connections += 1
        positions = results.decode_frame_payload_numpy(blob)
        if positions.shape[0] != total_vertices:
            raise results.ResultValidationError(
                f"frame {frame} has {positions.shape[0]} vertices, expected "
                f"{total_vertices}")
        yield FrameData(positions)


class OwnedLocalResultSource:
    kind = "owned_local"

    def __init__(self, work_directory: Path, project_name: str, *,
                 poll_interval: float, readiness_timeout: float = 5.0) -> None:
        self.root = owned_project_root(work_directory, project_name)
        self.poll_interval = max(0.001, poll_interval)
        self.readiness_timeout = readiness_timeout
        self.bytes_transferred = 0
        self.bytes_read = 0
        self.connections = 0

    def output_map_blob(self) -> bytes:
        path = contained_result_path(self.root, results.MAP_PATH, "map.pickle")
        blob = path.read_bytes()
        self.bytes_read += len(blob)
        return blob

    def _ready_frame(self, frame: int, expected_size: int,
                     check_cancel: Callable[[], None]) -> Path:
        relative = results.frame_file_path(frame)
        path = contained_result_path(self.root, relative, f"vert_{frame}.bin")
        deadline = time.monotonic() + self.readiness_timeout
        last_size: int | None = None
        while True:
            check_cancel()
            try:
                last_size = path.stat().st_size
            except FileNotFoundError:
                last_size = None
            if last_size == expected_size and last_size % 12 == 0:
                return path
            if last_size is not None and last_size % 12 != 0:
                raise results.ResultValidationError(
                    f"frame {frame} size {last_size} is not a multiple of 12")
            if time.monotonic() >= deadline:
                raise results.ResultValidationError(
                    f"frame {frame} was not ready within "
                    f"{self.readiness_timeout:.3f}s (size={last_size}, "
                    f"expected={expected_size})")
            time.sleep(self.poll_interval)

    @contextmanager
    def frame_positions(self, frame: int, total_vertices: int,
                        check_cancel: Callable[[], None]) -> Iterator[FrameData]:
        expected_size = total_vertices * 12
        path = self._ready_frame(frame, expected_size, check_cancel)
        with path.open("rb") as stream:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapping:
                positions = np.frombuffer(mapping, dtype=np.dtype("<f4")).reshape((-1, 3))
                frame_data = FrameData(positions)
                try:
                    if positions.shape != (total_vertices, 3):
                        raise results.ResultValidationError(
                            f"frame {frame} has an invalid position shape")
                    if not np.isfinite(positions).all():
                        raise results.ResultValidationError(
                            f"frame {frame} contains non-finite positions")
                    self.bytes_read += expected_size
                    yield frame_data
                finally:
                    frame_data.positions = None
                    del positions
