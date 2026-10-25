# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport debug views of the baked TETHYS state (display only).

The overlay reads the authenticated wetness cache for the current frame and
colors the evaluated cloth faces. It never writes mesh data, materials or
cache fingerprints. The draw handler exists only while an object shows a
preview, so ordinary Cloth NeXt sessions are unaffected.
"""
from __future__ import annotations

from pathlib import Path

import bpy
import numpy as np

from ..tethys import cache as tcache
from ..tethys import protocol

_handle = None
_batch_cache = {}


def invalidate(*_args) -> None:
    _batch_cache.clear()
    _sync_handler()
    for window in getattr(getattr(bpy.context, "window_manager", None), "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _previewing():
    for obj in getattr(bpy.context, "visible_objects", ()) or ():
        settings = getattr(obj, "cloth_next", None)
        tethys = getattr(settings, "tethys", None) if settings else None
        if tethys is not None and tethys.preview_mode != "NONE" and tethys.cache_stem:
            yield obj


def _sync_handler():
    global _handle
    wanted = any(True for _ in _previewing())
    if wanted and _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")
    elif not wanted and _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None


def _colors(values, low, high):
    t = np.clip((values - low) / max(high - low, 1e-12), 0.0, 1.0)
    # Dry grey -> blue -> cyan, readable on cloth.
    return np.stack((0.15 * (1 - t), 0.25 + 0.55 * t, 0.35 + 0.65 * t, np.full_like(t, 0.85)), axis=1)


def _frame_values(obj, frame):
    s = obj.cloth_next.tethys
    paths = tcache.CachePaths(Path(s.cache_root), s.cache_stem)
    with protocol.CacheReader(paths.cache) as reader:
        frame = min(max(frame, reader.first_frame), reader.last_frame)
        return reader.read(frame)


def _draw():
    import gpu
    from gpu_extras.batch import batch_for_shader
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    shader = gpu.shader.from_builtin("SMOOTH_COLOR")
    for obj in _previewing():
        s = obj.cloth_next.tethys
        key = (obj.name, scene.frame_current, s.preview_mode, round(s.preview_scale, 6))
        batches = _batch_cache.get(key)
        if batches is None:
            if len(_batch_cache) > 8:
                _batch_cache.clear()
            try:
                batches = _build(obj, depsgraph, scene.frame_current, shader, batch_for_shader)
            except (OSError, ValueError, KeyError):
                batches = ()
            _batch_cache[key] = batches
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("LESS_EQUAL")
        for batch in batches:
            batch.draw(shader)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")


def _build(obj, depsgraph, frame, shader, batch_for_shader):
    s = obj.cloth_next.tethys
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        count = len(mesh.loop_triangles)
        if count == 0 or len(mesh.polygons) != len(obj.data.polygons):
            return ()
        corners = np.empty(count * 3, np.int32)
        polygons = np.empty(count, np.int32)
        mesh.loop_triangles.foreach_get("vertices", corners)
        mesh.loop_triangles.foreach_get("polygon_index", polygons)
        positions = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", positions)
        positions = positions.reshape(-1, 3)
        matrix = np.asarray(obj.matrix_world, np.float32)
        world = positions @ matrix[:3, :3].T + matrix[:3, 3]
        data = _frame_values(obj, frame)
        mode = s.preview_mode
        scale = s.preview_scale
        if mode == "SURFACE":
            values = data.channels["surface"]
        elif mode == "SATURATION":
            values, scale = data.channels["saturation"], 1.0
        elif mode == "ABSORBED":
            values = data.channels["saturation"] * _capacity_mm(obj)
        elif mode == "CONTACT":
            values, scale = data.channels.get("contact", np.zeros(len(obj.data.polygons))), 1.0
        elif mode == "DRAINAGE":
            values = data.channels.get("drained", np.zeros(len(obj.data.polygons)))
        elif mode == "ZONES":
            values, scale = _zone_values(obj), 1.0
        else:  # FLOW is drawn as lines below over a faint surface tint
            values, scale = data.channels["surface"], max(scale, 1e-6)
        colors = _colors(np.asarray(values, np.float32)[polygons], 0.0, scale)
        tri_positions = world[corners]
        batches = [batch_for_shader(shader, "TRIS", {"pos": tri_positions,
                                                     "color": np.repeat(colors, 3, axis=0)})]
        if mode == "FLOW" and "flow" in data.channels:
            centers = np.zeros((len(obj.data.polygons), 3), np.float32)
            np.add.at(centers, polygons, tri_positions.reshape(-1, 3, 3).mean(axis=1))
            counts = np.bincount(polygons, minlength=len(centers)).astype(np.float32)
            centers /= np.maximum(counts, 1)[:, None]
            flow = data.channels["flow"]
            speed = np.linalg.norm(flow, axis=1)
            moving = speed > 1e-6
            length = np.minimum(speed[moving] * 0.5, 0.05)[:, None]
            ends = centers[moving] + flow[moving] / speed[moving, None] * length
            lines = np.empty((2 * moving.sum(), 3), np.float32)
            lines[0::2], lines[1::2] = centers[moving], ends
            line_colors = np.tile(np.array([[1.0, 0.85, 0.2, 1.0], [1.0, 0.4, 0.1, 1.0]], np.float32),
                                  (int(moving.sum()), 1))
            batches.append(batch_for_shader(shader, "LINES", {"pos": lines, "color": line_colors}))
        return tuple(batches)
    finally:
        evaluated.to_mesh_clear()


def _capacity_mm(obj):
    from . import tethys_bake
    mesh = obj.data
    owners = np.asarray(tethys_bake.zone_owners(obj))
    rows, material_of = tethys_bake.resolved_materials(obj, owners.tolist(), list(range(len(mesh.polygons))))
    capacity = np.array([row[1] * 1e3 for row in rows])
    return capacity[np.asarray(material_of)]


def _zone_values(obj):
    from . import tethys_bake
    mesh = obj.data
    owners = tethys_bake.zone_owners(obj)
    _rows, material_of = tethys_bake.resolved_materials(obj, list(owners), list(range(len(mesh.polygons))))
    values = np.asarray(material_of, np.float32)
    return values / max(1.0, float(values.max()))


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
    _batch_cache.clear()
