# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Frames where an animated collider touches itself (a hand pressed onto the hip, crossed legs).

Cloth caught between two body parts that come closer than the cloth is thick cannot stay free of
intersections in any solver. Such spots are reported to the artist as pinched instead of being
counted as simulation errors (Tim, 2026-11-04). Pure numpy (no Blender), run after a bake on the
collider frames that were sent to the engine.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Body parts are "far apart" when their vertices were at least this far apart in the first frame.
FAR_AT_REST = 0.05  # m
# Two far-apart parts touch when vertices of them come closer than this fraction of the median edge.
TOUCH_FRACTION = 0.6
MIN_VERTICES = 3     # touching vertices per frame before a frame counts


@dataclass(frozen=True)
class PinchReport:
    collider: str
    frames: tuple[int, ...]          # frame offsets (0 = first baked frame) where the collider touches itself
    worst_frame: int
    worst_count: int
    worst_center: tuple[float, float, float]


def _median_edge(world: np.ndarray, triangles: np.ndarray) -> float:
    t = np.asarray(triangles, np.int64).reshape(-1, 3)
    if not len(t):
        return 0.0
    e = np.linalg.norm(world[t[:, 0]] - world[t[:, 1]], axis=1)
    e = e[e > 0]
    return float(np.median(e)) if len(e) else 0.0


def _close_pairs(x: np.ndarray, radius: float) -> tuple[np.ndarray, np.ndarray]:
    """Index pairs (i < j) of points closer than radius (uniform grid, 27 neighbour cells)."""
    cell = np.floor(x / radius).astype(np.int64)
    cell -= cell.min(0)
    dims = cell.max(0) + 3
    key = (cell[:, 0] * dims[1] + cell[:, 1]) * dims[2] + cell[:, 2]
    order = np.argsort(key, kind="stable")
    skey = key[order]
    out_i, out_j = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                nkey = ((cell[:, 0] + dx) * dims[1] + cell[:, 1] + dy) * dims[2] + cell[:, 2] + dz
                lo = np.searchsorted(skey, nkey, "left")
                hi = np.searchsorted(skey, nkey, "right")
                counts = hi - lo
                if not counts.any():
                    continue
                src = np.repeat(np.arange(len(x)), counts)
                starts = np.repeat(lo, counts)
                offs = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
                dst = order[starts + offs]
                keep = src < dst
                src, dst = src[keep], dst[keep]
                near = np.einsum("ij,ij->i", x[src] - x[dst], x[src] - x[dst]) < radius * radius
                out_i.append(src[near])
                out_j.append(dst[near])
    if not out_i:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    return np.concatenate(out_i), np.concatenate(out_j)


def self_contact(name: str, rest: np.ndarray, triangles: np.ndarray, frames) -> PinchReport | None:
    """Frames in which far-apart parts of one collider touch (None: never, or a static collider)."""
    if frames is None or not len(frames):
        return None
    rest = np.asarray(rest, np.float64)
    edge = _median_edge(rest, triangles)
    if not edge > 0.0:
        return None
    radius = TOUCH_FRACTION * edge
    hits, worst = [], (0, 0, (0.0, 0.0, 0.0))
    for k, x in enumerate(frames):
        x = np.asarray(x, np.float64)
        i, j = _close_pairs(x, radius)
        if not len(i):
            continue
        far = np.einsum("ij,ij->i", rest[i] - rest[j], rest[i] - rest[j]) > FAR_AT_REST * FAR_AT_REST
        i, j = i[far], j[far]
        touching = np.unique(np.concatenate([i, j]))
        if len(touching) >= MIN_VERTICES:
            hits.append(k)
            if len(touching) > worst[1]:
                c = x[touching].mean(0)
                worst = (k, int(len(touching)), (float(c[0]), float(c[1]), float(c[2])))
    if not hits:
        return None
    return PinchReport(name, tuple(hits), worst[0], worst[1], worst[2])


def ranges(frames, first: int = 0) -> str:
    """'32-49, 55' for frame offsets plus the scene's first frame."""
    out, run = [], []
    for f in sorted(frames):
        if run and f == run[-1] + 1:
            run.append(f)
            continue
        if run:
            out.append(f"{run[0] + first}-{run[-1] + first}" if len(run) > 1 else f"{run[0] + first}")
        run = [f]
    if run:
        out.append(f"{run[0] + first}-{run[-1] + first}" if len(run) > 1 else f"{run[0] + first}")
    return ", ".join(out)
