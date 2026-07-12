# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Minimal PC2 (POINTCACHE2) writer for the Phase-3A playback cache.

Format verified against the pinned upstream ``blender_addon/core/pc2.py``
(commit ``7193f158``): 12-byte magic ``POINTCACHE2\\0``, little-endian
``version(i32)=1``, ``n_verts(i32)``, ``start(f32)``, ``sampling(f32)``,
``n_frames(i32)``, followed by ``n_frames * n_verts`` float32 XYZ triples in
the original vertex order. Published atomically via a temporary file.

This is the Phase-3 proof playback path, not the final Phase-4 cache system.
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

PC2_MAGIC = b"POINTCACHE2\0"
PC2_VERSION = 1
PC2_HEADER_SIZE = 32


class Pc2Error(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Pc2Header:
    vertex_count: int
    start_frame: float
    sample_rate: float
    frame_count: int


def write_pc2(path: Path, frames: Sequence[Sequence[Sequence[float]]], *,
              start_frame: float = 0.0, sample_rate: float = 1.0) -> Pc2Header:
    """Write all frames to ``path`` atomically and return the header."""
    if not frames:
        raise Pc2Error("at least one frame is required")
    vertex_count = len(frames[0])
    if vertex_count == 0:
        raise Pc2Error("frames must contain vertices")
    for index, frame in enumerate(frames):
        if len(frame) != vertex_count:
            raise Pc2Error(f"frame {index} has {len(frame)} vertices, "
                           f"expected {vertex_count} (constant topology)")
        for position in frame:
            if len(position) != 3 or any(not math.isfinite(c) for c in position):
                raise Pc2Error(f"frame {index} contains a non-finite position")
    header = struct.pack("<12sii f f i".replace(" ", ""), PC2_MAGIC,
                         PC2_VERSION, vertex_count, float(start_frame),
                         float(sample_rate), len(frames))
    body = bytearray()
    for frame in frames:
        for position in frame:
            body += struct.pack("<3f", float(position[0]), float(position[1]),
                                float(position[2]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(header)
        stream.write(bytes(body))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return Pc2Header(vertex_count, float(start_frame), float(sample_rate),
                     len(frames))


def read_header(path: Path) -> Pc2Header:
    """Validate the magic/version and return the parsed header."""
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
