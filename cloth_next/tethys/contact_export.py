# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Crop prepared GAIA water fields around the baked cloth (data access only, no bpy).

For each wetness frame the cloth's world bounding box is read from its PC2
and the matching ``.gaia`` frame is cropped to the nodes around it. The
engine interprets these nodes as contact; nothing here decides wetness.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import struct

import numpy as np

from ..gaia.container import GaiaContainer
from .protocol import WaterBlock, write_env

MARGIN_NODES = 2
REQUIRED_SAMPLING = "all-liquid-particles"


class ContactSourceError(ValueError):
    """The water source cannot provide a trustworthy contact signal."""


@dataclass(frozen=True)
class ContactSource:
    path: Path
    fingerprint: str
    fps: float
    frames: tuple[int, int]


class Pc2Frames:
    """Frame-by-frame PC2 reads. No memory map: Windows would otherwise lock
    the cloth cache against a later re-bake while Python still holds it."""

    def __init__(self, path: Path, vertex_count: int):
        self.path = Path(path)
        with self.path.open("rb") as stream:
            header = stream.read(32)
        if len(header) != 32 or header[:12] != b"POINTCACHE2\0":
            raise ContactSourceError("Cloth cache is not a PC2 file")
        version, count, _start, _rate, frames = struct.unpack("<iiffi", header[12:])
        if version != 1 or count != vertex_count or frames <= 0:
            raise ContactSourceError("Cloth cache does not match the mesh")
        if self.path.stat().st_size != 32 + frames * count * 12:
            raise ContactSourceError("Cloth cache size does not match its header")
        self.frame_count, self.vertex_count = frames, count

    def read(self, sample: int) -> np.ndarray:
        if not 0 <= sample < self.frame_count:
            raise ContactSourceError("The cloth cache does not cover the wetness frame range")
        size = self.vertex_count * 12
        with self.path.open("rb") as stream:
            stream.seek(32 + sample * size)
            data = stream.read(size)
        if len(data) != size:
            raise ContactSourceError("Cloth cache is truncated")
        return np.frombuffer(data, dtype="<f4").reshape(-1, 3).astype(float)


def inspect_source(path: Path) -> ContactSource:
    path = Path(path)
    if not path.is_file():
        raise ContactSourceError("Prepare the GAIA Fluid Cache first; TETHYS reads its water field")
    try:
        with GaiaContainer(path) as container:
            water = container.manifest["water"]
            frames = [int(f["frame"]) for f in container.manifest["frames"]]
    except (OSError, ValueError, KeyError) as exc:
        raise ContactSourceError("The GAIA Fluid Cache is damaged. Prepare it again") from exc
    sampling = (water.get("provider") or {}).get("sampling")
    if sampling != REQUIRED_SAMPLING:
        raise ContactSourceError(
            "Wetness contact needs a Fluid Cache built from FLIP fluid particles. Enable Fluid "
            "Particle export with velocity in FLIP, rebake it and prepare the Fluid Cache again")
    if frames != list(range(frames[0], frames[0] + len(frames))):
        raise ContactSourceError("The GAIA Fluid Cache frames are not contiguous. Prepare it again")
    fps = float(water.get("fps", 0))
    if not math.isfinite(fps) or fps <= 0:
        raise ContactSourceError("The GAIA Fluid Cache has no valid frame rate. Prepare it again")
    return ContactSource(path, str(water.get("fingerprint", "")), fps, (frames[0], frames[-1]))


def crop(field, lower, upper):
    """Cropped WaterBlock covering the world box [lower, upper], or None."""
    minimum = np.asarray(field.minimum, float)
    maximum = np.asarray(field.maximum, float)
    nz, ny, nx = field.influence.shape
    dims = np.array([nx, ny, nz])
    spacing = (maximum - minimum) / (dims - 1)
    start = np.floor((np.asarray(lower) - minimum) / spacing).astype(int) - MARGIN_NODES
    stop = np.ceil((np.asarray(upper) - minimum) / spacing).astype(int) + MARGIN_NODES + 1
    start = np.clip(start, 0, dims - 1)
    stop = np.clip(stop, 0, dims)
    if np.any(stop - start < 2):
        return None
    sx, sy, sz = (slice(int(a), int(b)) for a, b in zip(start, stop))
    influence = np.asarray(field.influence[sz, sy, sx], dtype=np.float32)
    if not influence.any():
        return None
    velocity = np.asarray(field.velocity[sz, sy, sx], dtype=np.float32)
    origin = minimum + spacing * start
    return WaterBlock(tuple(float(v) for v in origin), tuple(float(v) for v in spacing),
                      influence, velocity)


def export_contact(destination: Path, source: ContactSource, pc2_path: Path, vertex_count: int,
                   matrix_world, first_frame: int, last_frame: int, pc2_first_sample: int, *,
                   check_cancel=None) -> int:
    """Write the engine's water-contact stream; returns the number of frames with liquid."""
    if first_frame < source.frames[0] or last_frame > source.frames[1]:
        raise ContactSourceError(
            f"The GAIA Fluid Cache covers frames {source.frames[0]}–{source.frames[1]}; "
            f"the wetness bake needs {first_frame}–{last_frame}. Prepare the Fluid Cache for the full range")
    positions = Pc2Frames(pc2_path, vertex_count)
    frame_count = last_frame - first_frame + 1
    if pc2_first_sample < 0 or pc2_first_sample + frame_count > positions.frame_count:
        raise ContactSourceError("The cloth cache does not cover the wetness frame range. Bake the cloth again")
    matrix = np.asarray(matrix_world, dtype=float)
    wet_frames = 0

    def blocks():
        nonlocal wet_frames
        with GaiaContainer(source.path) as container:
            index_of = {int(f["frame"]): k for k, f in enumerate(container.manifest["frames"])}
            for offset in range(frame_count):
                local = positions.read(pc2_first_sample + offset)
                world = local @ matrix[:3, :3].T + matrix[:3, 3]
                field = container.frame(index_of[first_frame + offset])
                block = crop(field, world.min(axis=0), world.max(axis=0))
                wet_frames += block is not None
                yield block
                del field, block

    write_env(destination, first_frame, frame_count, source.fps, blocks(), check_cancel=check_cancel)
    return wet_frames
