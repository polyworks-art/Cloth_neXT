# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender Mantaflow liquid caches as a GAIA water source. No Blender import.

Mantaflow stores the liquid velocity as a staggered (MAC) OpenVDB grid in
its own cell units; GAIA needs volumetric liquid samples in world m/s plus the
closed water surface per frame. This module converts one frame:

* velocity: the saved grid is in cells per Mantaflow time unit. Blender's
  fluid_script.h sets ``ratioBTimeToTimestep = 1/frameLengthRaw = 1/2.5``, so
  one unit is 0.4 s whatever the scene fps: ``v_m_s = v_cells * dx * 2.5``.
  Measured on real free-fall bakes: frame 7 reads 2.87 m/s at 24 fps (g*t =
  2.86) and 1.44 m/s at 48 fps (1.43). These are simulation seconds; Time
  Scale must be 1 so they are playback seconds too.
* liquid samples: 2x2x2 points per liquid cell (cell centre inside the
  surface mesh), carrying the face-averaged cell velocity.
* surface + bounds: written as a FLIP-style ``bakefiles`` folder (BOBJ
  triangles in world metres, ``boundsNNNNNN.bbox``) so the existing GAIA
  Hydrodynamic Pressure preparation reads Mantaflow exactly like FLIP.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct

import numpy as np

from .voxelize import inside_voxels

UNITS_PER_SECOND = 2.5  # 1 / (0.4 s per Mantaflow time unit)
SUBSAMPLES = 2  # liquid samples per cell and axis


def data_file(cache_directory, frame: int) -> Path:
    return Path(cache_directory) / "data" / f"fluid_data_{frame:04d}.vdb"


def read_velocity(path, *, openvdb=None):
    """(cell-centred velocity [x,y,z,3] in Mantaflow cell units, index origin (i,j,k)).

    The file grid is staggered: component c lives on the lower face of each
    cell along axis c, so a cell centre is the mean of its two faces.
    """
    if openvdb is None:
        import openvdb  # bundled with Blender
    try:
        grid = openvdb.read(str(path), "velocity")
    except (KeyError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Mantaflow velocity grid is missing in {Path(path).name}") from exc
    if grid.activeVoxelCount() == 0:
        return np.zeros((0, 0, 0, 3), np.float32), (0, 0, 0)
    lo, hi = grid.evalActiveVoxelBoundingBox()
    lo = tuple(int(v) for v in lo)
    hi = tuple(int(v) + 1 for v in hi)  # one more face for the centre average
    faces = np.zeros(tuple(h - l + 1 for l, h in zip(lo, hi)) + (3,), np.float32)
    grid.copyToArray(faces, ijk=lo)
    centre = np.empty(tuple(n - 1 for n in faces.shape[:3]) + (3,), np.float32)
    centre[..., 0] = .5 * (faces[:-1, :-1, :-1, 0] + faces[1:, :-1, :-1, 0])
    centre[..., 1] = .5 * (faces[:-1, :-1, :-1, 1] + faces[:-1, 1:, :-1, 1])
    centre[..., 2] = .5 * (faces[:-1, :-1, :-1, 2] + faces[:-1, :-1, 1:, 2])
    if not np.isfinite(centre).all():
        raise ValueError(f"Mantaflow velocity is not finite in {Path(path).name}")
    return centre, lo


def liquid_samples(velocity_cells, index_origin, *, grid_minimum, dx, vertices, faces):
    """World positions and m/s velocities of the liquid, 8 samples per liquid cell.

    ``grid_minimum`` is the world position of cell (0,0,0)'s lower corner,
    ``vertices``/``faces`` the closed liquid surface in world metres.
    """
    v = np.asarray(velocity_cells, np.float32)
    if v.size == 0 or len(faces) == 0:
        return np.empty((0, 3)), np.empty((0, 3))
    nx, ny, nz = v.shape[:3]
    lo = np.asarray(grid_minimum, float) + np.asarray(index_origin, float) * dx
    inside = inside_voxels(vertices, faces, lo, (dx, dx, dx), (nx, ny, nz)).transpose(2, 1, 0)
    cells = np.argwhere(inside)
    if not len(cells):
        return np.empty((0, 3)), np.empty((0, 3))
    scale = dx * UNITS_PER_SECOND
    velocity = v[cells[:, 0], cells[:, 1], cells[:, 2]].astype(float) * scale
    offsets = (np.stack(np.meshgrid(*[np.arange(SUBSAMPLES)] * 3, indexing="ij"), -1)
               .reshape(-1, 3) + .5) / SUBSAMPLES
    positions = lo + (cells[:, None, :] + offsets[None]) * dx
    return positions.reshape(-1, 3), np.repeat(velocity, len(offsets), axis=0)


def write_bobj(path, vertices, faces) -> None:
    """FLIP BOBJ: int32 n, n*3 float32 vertices, int32 m, m*3 int32 triangles."""
    x = np.ascontiguousarray(vertices, "<f4")
    f = np.ascontiguousarray(faces, "<i4")
    data = (struct.pack("<i", len(x)) + x.tobytes() + struct.pack("<i", len(f)) + f.tobytes())
    _atomic(Path(path), data)


def write_bounds(path, minimum, maximum, dx) -> None:
    """FLIP ``boundsNNNNNN.bbox`` for a world-aligned grid starting at ``minimum``."""
    lo, hi = np.asarray(minimum, float), np.asarray(maximum, float)
    span = hi - lo
    sizes = np.maximum(1, np.rint(span / dx)).astype(int)
    record = {"x": lo[0], "y": lo[1], "z": lo[2], "width": span[0], "height": span[1],
              "depth": span[2], "dx": float(dx), "isize": int(sizes[0]), "jsize": int(sizes[1]),
              "ksize": int(sizes[2])}
    _atomic(Path(path), json.dumps({k: (float(v) if isinstance(v, (float, np.floating)) else v)
                                    for k, v in record.items()}).encode())


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)
