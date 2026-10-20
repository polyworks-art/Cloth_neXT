# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached world-space sewing strokes and pixel-space notches and endpoints.

Geometry is rebuilt only when a seam's paths, direction, mesh or on-screen
thread density change. Per frame the renderer projects the few path points
it needs and binds cached batches.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import math

import numpy as np

from ..sewing_pick import project
from ..sewing_visual import sample, seam_color, thread_count, threads

_log = logging.getLogger(__name__)

INVALID = (1.0, .16, .18)
PREVIEW = "active-seam"


@dataclass(frozen=True)
class SeamGeometry:
    """Display positions of one seam, as shown in the viewport."""
    identity: str
    version: tuple
    first: tuple
    second: tuple
    flipped: bool = False
    invalid: bool = False
    message: str = ""


def quantized_count(pixels, density):
    """Thread count on a quarter-octave ladder so zooming rarely rebuilds."""
    count = thread_count(pixels, density)
    if count <= 8:
        return count
    return max(2, min(1024, int(round(2 ** (round(math.log2(count) * 4) / 4)))))


def _projected_length(view, points):
    if len(points) < 2:
        return 0.0
    projected = project(view.matrix, points, view.width, view.height)
    steps = np.diff(projected, axis=0)
    lengths = np.sqrt((steps ** 2).sum(axis=1))
    return float(np.nansum(lengths))


class SewingRenderer:
    def __init__(self):
        self.batches = {}
        self.shader = None
        self.flat = None

    def clear(self):
        self.batches.clear()
        _anchors.clear()

    def _batch(self, key, positions):
        from gpu_extras.batch import batch_for_shader
        batch = self.batches.get(key)
        if batch is None:
            if len(self.batches) > 512:
                self.batches.clear()
            batch = batch_for_shader(self.shader, "LINES", {"pos": positions})
            self.batches[key] = batch
        return batch

    def draw(self, context, view, seam, *, selected=False, emphasis=1.0):
        """World-space side highlights and correspondence threads."""
        import gpu
        settings = context.scene.cloth_next_sewing_visual
        if not (seam.first or seam.second):
            return
        if self.shader is None:
            self.shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        shader = self.shader
        color = INVALID if seam.invalid else seam_color(seam.identity)
        alpha = (1.0 if selected else .45) * emphasis
        old = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
        try:
            gpu.state.blend_set("ALPHA")
            gpu.state.depth_mask_set(False)
            shader.bind()
            shader.uniform_float("viewportSize", (view.width, view.height))
            # Occluded parts stay faintly visible; visible parts draw on top
            # with depth testing so seams read correctly around a body.
            passes = ((("NONE", 1.0),) if settings.xray
                      else (("NONE", .22), ("LESS_EQUAL", 1.0)))
            batches = []
            if settings.lines:
                key = (seam.identity, seam.version, "edges")
                positions = self.batches.get(key) is None and tuple(
                    point for path in (seam.first, seam.second)
                    for pair in zip(path, path[1:]) for point in pair)
                if positions or key in self.batches:
                    batches.append((self._batch(key, positions), 3.0 if selected else 2.0, alpha))
            if settings.threads and len(seam.first) > 1 and len(seam.second) > 1:
                pixels = max(_projected_length(view, seam.first),
                             _projected_length(view, seam.second))
                count = quantized_count(pixels, settings.density)
                key = (seam.identity, seam.version, "threads", count, seam.flipped)
                positions = None
                if key not in self.batches:
                    positions = threads(seam.first, seam.second, count, seam.flipped)
                if positions or key in self.batches:
                    batches.append((self._batch(key, positions), 1.0, settings.opacity
                                    * (1.0 if selected else .5) * emphasis))
            for depth, factor in passes:
                gpu.state.depth_test_set(depth)
                for batch, width, opacity in batches:
                    shader.uniform_float("lineWidth", width)
                    shader.uniform_float("color", (*color, opacity * factor))
                    batch.draw(shader)
        finally:
            gpu.state.blend_set(old[0])
            gpu.state.depth_test_set(old[1])
            gpu.state.depth_mask_set(old[2])

    def draw_pixels(self, context, view, seam, *, selected=False, emphasis=1.0):
        """Directional notches and endpoint circles at constant pixel size."""
        import gpu
        from gpu_extras.batch import batch_for_shader
        settings = context.scene.cloth_next_sewing_visual
        color = INVALID if seam.invalid else seam_color(seam.identity)
        alpha = (.95 if selected else .55) * emphasis
        segments = pixel_segments(view, seam, notches=settings.notches,
                                  endpoints=settings.lines, selected=selected)
        if not len(segments):
            return
        if self.flat is None:
            self.flat = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        positions = np.zeros((len(segments), 3), dtype=np.float32)
        positions[:, :2] = segments
        old = gpu.state.blend_get()
        try:
            gpu.state.blend_set("ALPHA")
            self.flat.bind()
            self.flat.uniform_float("viewportSize", (view.width, view.height))
            self.flat.uniform_float("lineWidth", 1.6 if selected else 1.2)
            self.flat.uniform_float("color", (*color, alpha))
            batch_for_shader(self.flat, "LINES", {"pos": positions}).draw(self.flat)
        finally:
            gpu.state.blend_set(old)


_STEPS = 16
_UNIT = np.stack((np.cos(np.arange(_STEPS) * math.tau / _STEPS),
                  np.sin(np.arange(_STEPS) * math.tau / _STEPS)), axis=1)
_NOTCH_AT = np.array((2, 6, 10, 14))


_anchors = {}


def _seam_anchors(seam):
    """World points the pixel pass projects: 17 samples per side plus ends.

    Cached per seam version so a redraw does one small projection per seam.
    """
    key = (seam.identity, seam.version, seam.flipped)
    cached = _anchors.get(key)
    if cached is None:
        paths = (seam.first, seam.second[::-1] if seam.flipped else seam.second)
        samples = [np.asarray(sample(path, 17), dtype=np.float64).reshape(-1, 3)
                   for path in paths]
        ends = np.asarray([point for path in paths if path for point in (path[0], path[-1])],
                          dtype=np.float64).reshape(-1, 3)
        cached = (samples, ends)
        if len(_anchors) > 512:
            _anchors.clear()
        _anchors[key] = cached
    return cached


def pixel_segments(view, seam, *, notches=True, endpoints=True, selected=False):
    """Line-segment endpoints (N, 2) in region pixels for notches and markers."""
    samples, ends = _seam_anchors(seam)
    world = np.concatenate([*samples, ends]) if len(ends) or any(len(x) for x in samples) else None
    if world is None or not len(world):
        return np.zeros((0, 2))
    projected = project(view.matrix, world, view.width, view.height)
    pieces, offset = [], 0
    for side in samples:
        side_points = projected[offset:offset + len(side)]
        offset += len(side)
        if not notches or len(side_points) < 17:
            continue
        a, b = side_points[_NOTCH_AT], side_points[_NOTCH_AT + 1]
        tangent = b - a
        norm = np.sqrt((tangent ** 2).sum(axis=1))
        keep = ~np.isnan(norm) & (norm > .01)
        if not keep.any():
            continue
        a, tangent = a[keep], tangent[keep] / norm[keep, None]
        normal = np.stack((-tangent[:, 1], tangent[:, 0]), axis=1)
        tip = a + tangent * 3
        for wing in (normal, -normal):
            base = a - tangent * 4 + wing * 4
            pieces.append(np.stack((base, tip), axis=1).reshape(-1, 2))
    if endpoints and len(ends):
        centers = projected[offset:]
        starts = np.zeros(len(centers), dtype=bool)
        starts[::2] = True          # start markers get an inner ring
        valid = ~np.isnan(centers).any(axis=1)
        radius = 4.5 if selected else 3.5
        for r, mask in ((radius, valid), (radius * .45, valid & starts)):
            if not mask.any():
                continue
            ring = centers[mask][:, None, :] + r * _UNIT[None]
            nxt = np.roll(ring, -1, axis=1)
            pieces.append(np.stack((ring, nxt), axis=2).reshape(-1, 2))
    if not pieces:
        return np.zeros((0, 2))
    return np.concatenate(pieces)


renderer = SewingRenderer()
