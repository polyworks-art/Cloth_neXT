# SPDX-License-Identifier: GPL-3.0-or-later
"""Cached world-space sewing strokes and pixel-space directional notches."""
import logging
import math
import bpy
from ..sewing_visual import length, sample, threads, thread_count, seam_color

_log = logging.getLogger(__name__)


class SewingRenderer:
    def __init__(self):
        self.batches = {}
        self.shader = None

    def clear(self):
        self.batches.clear()

    def draw(self, first, second, identity, *, selected=False, flipped=False, invalid=False):
        import gpu
        from gpu_extras.batch import batch_for_shader
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        from mathutils import Vector
        context = bpy.context
        settings = context.scene.cloth_next_sewing_visual
        project = lambda p: location_3d_to_region_2d(context.region, context.region_data, Vector(p))
        projected = [project(p) for p in first]
        pixels = sum((b-a).length for a, b in zip(projected, projected[1:]) if a is not None and b is not None)
        count = thread_count(pixels, settings.density)
        color = (1, .12, .14) if invalid else seam_color(identity)
        alpha = 1.0 if selected else .42
        if self.shader is None:
            self.shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        shader = self.shader
        old = (gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.depth_mask_get())
        try:
            gpu.state.blend_set('ALPHA')
            gpu.state.depth_test_set('NONE' if settings.xray else 'LESS_EQUAL')
            gpu.state.depth_mask_set(False)
            shader.bind()
            shader.uniform_float('viewportSize', (context.region.width, context.region.height))
            geometries = []
            if settings.lines:
                geometries.append(('edges', tuple(p for path in (first, second) for pair in zip(path, path[1:]) for p in pair), 2.4 if selected else 1.6, alpha))
                # Camera-facing endpoint circles, measured in pixels but drawn
                # at seam depth so the normal occlusion policy still applies.
                from bpy_extras.view3d_utils import region_2d_to_location_3d
                rings = []
                radius = 4.0 if selected else 3.25
                for path in (first, second):
                    if not path:
                        continue
                    for endpoint in (path[0], path[-1]):
                        center = project(endpoint)
                        if center is None:
                            continue
                        circle = tuple(tuple(region_2d_to_location_3d(
                            context.region, context.region_data,
                            center + Vector((math.cos(i * math.tau / 24),
                                             math.sin(i * math.tau / 24))) * radius,
                            Vector(endpoint))) for i in range(24))
                        rings.extend(p for i in range(24)
                                     for p in (circle[i], circle[(i + 1) % 24]))
                geometries.append(('endpoints', tuple(rings), 1.3, alpha))
            if settings.threads and first and second:
                geometries.append(('threads', threads(first, second, count, flipped), 1.0, settings.opacity * (1 if selected else .55)))
            for kind, positions, width, opacity in geometries:
                if not positions:
                    continue
                key = (identity, kind, tuple(first), tuple(second), count, flipped)
                if kind == 'endpoints':
                    key += (positions,)
                batch = self.batches.get(key)
                if batch is None:
                    batch = batch_for_shader(shader, 'LINES', {'pos': positions})
                    if len(self.batches) > 256:
                        self.batches.clear()
                    self.batches[key] = batch
                shader.uniform_float('lineWidth', width)
                shader.uniform_float('color', (*color, opacity))
                batch.draw(shader)
        finally:
            gpu.state.blend_set(old[0])
            gpu.state.depth_test_set(old[1])
            gpu.state.depth_mask_set(old[2])

    def notches(self, first, second, identity, flipped=False):
        """Project tangent arrows: constant pixel size and identical endpoint direction."""
        import gpu
        from gpu_extras.batch import batch_for_shader
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        from mathutils import Vector
        context = bpy.context
        if not context.scene.cloth_next_sewing_visual.notches:
            return
        positions = []
        for path in (first, second[::-1] if flipped else second):
            points = sample(path, 17)
            for i in (1, 5, 9, 13):
                if not points:
                    continue
                a, b = (location_3d_to_region_2d(context.region, context.region_data, Vector(points[j])) for j in (i, i+1))
                if a is None or b is None or (b-a).length < .01:
                    continue
                tangent = (b-a).normalized()
                normal = Vector((-tangent.y, tangent.x))
                positions.extend((a-tangent*5+normal*3, a+tangent*2, a+tangent*2, a-tangent*5-normal*3))
        if positions:
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            old = gpu.state.blend_get()
            try:
                gpu.state.blend_set('ALPHA')
                shader.bind()
                shader.uniform_float('color', (*seam_color(identity), .95))
                batch_for_shader(shader, 'LINES', {'pos': positions}).draw(shader)
            finally:
                gpu.state.blend_set(old)


renderer = SewingRenderer()
