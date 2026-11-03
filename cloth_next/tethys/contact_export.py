# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Crop prepared GAIA water fields around the baked cloth (data access only, no bpy).

For each wetness frame the cloth's world bounding box is read from its PC2
and the matching ``.gaia`` frame is cropped to the nodes around it. The
engine interprets these nodes as contact; nothing here decides wetness.

Where the water source also has its liquid surface mesh (FLIP ``bakefiles``,
or the surface folder GAIA writes beside the ``.gaia``), the cropped nodes
carry the signed distance to that surface instead of the particle support.
Particle splats reach about one grid spacing beyond the liquid, which put
the water line on the cloth one cell above the real water.

The surface distance is sampled on its own, finer grid (about the liquid
simulation's own cell size). On the coarse GAIA grid the half-contact band
was several centimetres wide, so cloth resting a centimetre inside the
liquid next to a body only reached partial contact and its water read weak.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import struct

import numpy as np

from ..gaia.container import GaiaContainer
from ..gaia.surface import read_surface
from ..gaia.surface_distance import contact_support, signed_distance
from .protocol import WaterBlock, write_env

MARGIN_NODES = 2
# Surface-contact grid: this share of the liquid simulation's cell size,
# never coarser than the GAIA field, and at most this many nodes per frame.
SURFACE_CELL_SHARE = 0.75
SURFACE_MAX_NODES = 400_000
REQUIRED_SAMPLING = "all-liquid-particles"
# Part of the wetness cache identity: a new recipe re-bakes old caches.
SURFACE_RECIPE = "liquid-surface-signed-distance-v2"
PARTICLE_RECIPE = "particle-support-v1"


class ContactSourceError(ValueError):
    """The water source cannot provide a trustworthy contact signal."""


@dataclass(frozen=True)
class ContactSource:
    path: Path
    fingerprint: str
    fps: float
    frames: tuple[int, int]
    surfaces: Path | None = None          # folder of NNNNNN.bobj + boundsNNNNNN.bbox
    cache_matrix: tuple | None = None     # FLIP cache-local -> world
    liquid_cell: float | None = None      # liquid simulation cell size, world units

    @property
    def recipe(self) -> str:
        return SURFACE_RECIPE if self.surfaces is not None else PARTICLE_RECIPE

    def surface_stamps(self, first: int, last: int) -> list:
        """Names, sizes and modification times of the surfaces a bake reads."""
        if self.surfaces is None:
            return []
        rows = []
        for frame in range(first, last + 1):
            for path in _surface_files(self.surfaces, frame):
                stat = path.stat()
                rows.append([path.name, stat.st_size, stat.st_mtime_ns])
        return rows

    def surface(self, frame: int):
        """World-space liquid surface (vertices, faces) of ``frame``."""
        mesh, bounds = _surface_files(self.surfaces, frame)
        try:
            vertices, faces, _digest = read_surface(mesh)
            raw = json.loads(bounds.read_text(encoding="utf-8"))
            offset = np.asarray([raw["x"], raw["y"], raw["z"]], dtype=np.float32)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ContactSourceError(f"The liquid surface of frame {frame} cannot be read: {exc}") from exc
        matrix = np.asarray(self.cache_matrix, dtype=float)
        # Same float32 translation as the FLIP loader applies before the
        # cache object's world transform (see gaia.particle_stream).
        local = (vertices - offset).astype(float)
        return local @ matrix[:3, :3].T + matrix[:3, 3], faces


def _surface_files(folder: Path, frame: int) -> tuple[Path, Path]:
    return folder / f"{frame:06d}.bobj", folder / f"bounds{frame:06d}.bbox"


def _surface_folder(provider: dict, frames: list[int]):
    """The liquid surface folder and cache matrix, when every frame has one."""
    folder, matrix = provider.get("cache"), provider.get("cache_matrix")
    if not folder or matrix is None:
        return None, None
    try:
        matrix = np.asarray(matrix, dtype=float)
    except (TypeError, ValueError):
        return None, None
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:3, :3])) < 1e-12:
        return None, None
    folder = Path(folder)
    if not all(path.is_file() for frame in frames for path in _surface_files(folder, frame)):
        return None, None
    return folder, tuple(map(tuple, matrix.tolist()))


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
    surfaces, matrix = _surface_folder(water.get("provider") or {}, frames)
    try:
        liquid_cell = float((water.get("grid_layout") or {}).get("source_voxel_size"))
    except (TypeError, ValueError):
        liquid_cell = None
    if liquid_cell is not None and not (math.isfinite(liquid_cell) and liquid_cell > 0):
        liquid_cell = None
    return ContactSource(path, str(water.get("fingerprint", "")), fps, (frames[0], frames[-1]),
                         surfaces, matrix, liquid_cell)


def surface_spacing(field_spacing, liquid_cell, lower, upper):
    """Node spacing of the surface-contact grid for the box [lower, upper]."""
    coarse = float(np.min(field_spacing))
    fine = SURFACE_CELL_SHARE * liquid_cell if liquid_cell else coarse / 3.0
    fine = min(fine, coarse)
    extent = np.maximum(np.asarray(upper, float) - np.asarray(lower, float), 0.0)
    while np.prod(np.ceil(extent / fine) + 2 * MARGIN_NODES + 1) > SURFACE_MAX_NODES and fine < coarse:
        fine = min(coarse, fine * 1.25)
    return fine


def _resample(values, minimum, spacing, points):
    """Trilinear samples of a (nz, ny, nx, ...) node array at world ``points``."""
    nz, ny, nx = values.shape[:3]
    dims = np.array([nx, ny, nz])
    q = np.clip((points - minimum) / spacing, 0, dims - 1)
    base = np.minimum(np.floor(q).astype(np.int64), dims - 2)
    frac = q - base
    out = 0.0
    for dz in (0, 1):
        for dy in (0, 1):
            for dx in (0, 1):
                weight = ((frac[:, 0] if dx else 1 - frac[:, 0]) * (frac[:, 1] if dy else 1 - frac[:, 1])
                          * (frac[:, 2] if dz else 1 - frac[:, 2]))
                sample = values[base[:, 2] + dz, base[:, 1] + dy, base[:, 0] + dx]
                out = out + (weight[:, None] * sample if sample.ndim > 1 else weight * sample)
    return out


def crop(field, lower, upper, surface=None, liquid_cell=None):
    """Cropped WaterBlock covering the world box [lower, upper], or None.

    With ``surface`` (world vertices, faces) the block is a finer grid whose
    nodes carry the liquid surface's signed distance as 0..1 support (0.5 on
    the surface) and the GAIA velocity resampled onto them.
    """
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
    origin = minimum + spacing * start
    if surface is not None:
        fine = surface_spacing(spacing, liquid_cell, lower, upper)
        origin = np.asarray(lower, float) - MARGIN_NODES * fine
        counts = (np.ceil((np.asarray(upper, float) - np.asarray(lower, float)) / fine).astype(int)
                  + 2 * MARGIN_NODES + 1)
        nx, ny, nz = (int(n) for n in np.maximum(counts, 2))
        fine_spacing = np.full(3, fine)
        influence = contact_support(signed_distance(*surface, origin, fine_spacing, (nz, ny, nx)), fine_spacing)
        if not influence.any():
            return None
        gz, gy, gx = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
        nodes = origin + np.stack((gx.ravel(), gy.ravel(), gz.ravel()), axis=1) * fine
        velocity = _resample(np.asarray(field.velocity, np.float32), minimum, spacing, nodes)
        velocity = velocity.reshape(nz, ny, nx, 3).astype(np.float32)
        return WaterBlock(tuple(float(v) for v in origin), tuple(float(v) for v in fine_spacing),
                          influence, velocity)
    velocity = np.asarray(field.velocity[sz, sy, sx], dtype=np.float32)
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
                surface = source.surface(first_frame + offset) if source.surfaces is not None else None
                block = crop(field, world.min(axis=0), world.max(axis=0), surface, source.liquid_cell)
                wet_frames += block is not None
                yield block
                del field, block, surface

    write_env(destination, first_frame, frame_count, source.fps, blocks(), check_cancel=check_cancel,
              compact=True)
    return wet_frames
