# SPDX-License-Identifier: GPL-3.0-or-later
"""Timeline Wind authoring, bounded GAIA export and live vector preview."""
import hashlib
import math
from pathlib import Path
import bpy
import numpy as np
from ..gaia.wind_field import (WindSource, velocity, sample_points, encode_grid,
                               arrow_lines, MAX_SCHEDULE_BYTES)

_handle = None


def force_objects(context):
    return tuple(obj for obj in context.scene.objects if obj.type == 'EMPTY'
                 and hasattr(obj, 'cloth_next') and obj.cloth_next.enabled
                 and obj.cloth_next.role == 'FORCE')


def source_at(context, obj):
    from . import solver_test
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    force = evaluated.cloth_next.force
    matrix = evaluated.matrix_world
    frame = context.scene.frame_current + context.scene.frame_subframe
    speed = float(force.wind_strength)
    if force.wind_variation:
        speed += force.wind_variation * solver_test._wind_oscillation(
            obj, frame, solver_test._scene_fps(context), force.wind_noise_scale)
    try:
        return WindSource(tuple(matrix.translation), tuple(matrix.col[2][:3]),
            speed, force.wind_radius, force.wind_falloff, force.wind_turbulence,
            force.wind_turbulence_size, force.wind_noise_scale,
            int.from_bytes(hashlib.sha256(obj.name_full.encode()).digest()[:4], 'little'))
    except ValueError as exc:
        raise solver_test.SceneValidationError(f'{obj.name}: {exc}') from exc


def apply_wind_payload(context, payload, resolved, first, last):
    from . import solver_test
    from ..ppf.schema import envelope
    objects = [obj for obj in force_objects(context) if obj.cloth_next.force.wind_local]
    if not objects:
        return payload, None
    if str(getattr(resolved, 'protocol_version', '')) != '0.23':
        raise solver_test.SceneValidationError('Local Wind and Turbulence require official GAIA 0.23. Select it in Solver Installations.')
    raw = payload.read_bytes() if isinstance(payload, Path) else payload
    tree = envelope.loads_envelope(raw, envelope.KIND_PARAM, schema_version=2)
    if float(tree['scene'].get('air-density', .001)) <= 0:
        raise solver_test.SceneValidationError('Local Wind needs positive Air Density.')
    original = context.scene.frame_current, context.scene.frame_subframe
    samples = {obj.name: [] for obj in objects}
    fps = solver_test._scene_fps(context)
    try:
        for frame in range(int(first), int(last) + 1):
            solver_test._check_preparation_cancel()
            context.scene.frame_set(frame)
            for obj in objects:
                samples[obj.name].append(source_at(context, obj))
    finally:
        context.scene.frame_set(original[0], subframe=original[1])
    field = tree.setdefault('force_field', {'grids': [], 'scripts': []})
    used = sum(int(np.prod(grid['shape'])) * 4 for grid in field['grids'])
    for obj in objects:
        records = samples[obj.name]
        centers = np.asarray([source.center for source in records])
        radii = np.asarray([source.radius for source in records])[:, None]
        lo = (centers - radii).min(axis=0); hi = (centers + radii).max(axis=0)
        resolution = int(obj.cloth_next.force.wind_resolution)
        spacing = 2 * radii.min() / (resolution - 1)
        dims = np.maximum(2, np.ceil((hi - lo) / spacing).astype(np.int64) + 1)
        size = len(records) * math.prod(map(int, dims)) * 3 * 4
        used += size
        if used > MAX_SCHEDULE_BYTES:
            raise solver_test.SceneValidationError('Local Wind schedule exceeds 32 MiB. Shorten the Bake range or lower Field Resolution; moving fields also need more grid space.')
        points = sample_points(lo, hi, dims)
        values = np.empty((len(records), *points.shape), dtype=np.float32)
        for index, source in enumerate(records):
            solver_test._check_preparation_cancel()
            values[index] = velocity(points, (source,), (first + index) / fps)
        field['grids'].append(encode_grid(values, lo, hi, [index / fps for index in range(len(records))]))
        del values, points
    for params, _names, _uuids in tree.get('group', ()):
        params.setdefault('force-field-weight', 1.)
    encoded = envelope.dumps_envelope(envelope.KIND_PARAM, tree, schema_version=2)
    return encoded, hashlib.sha256(encoded).hexdigest()


def preview(context):
    """Read the current evaluated frame; never bake, change frame or start a solver."""
    from . import solver_test
    forces = force_objects(context)
    visible = [obj for obj in forces if obj.cloth_next.force.wind_show_vectors]
    if not visible:
        return np.empty((0, 3), np.float32)
    sources = [source_at(context, obj) for obj in forces if obj.cloth_next.force.wind_local]
    centers = np.asarray([tuple(obj.matrix_world.translation) for obj in visible])
    radii = np.asarray([obj.cloth_next.force.wind_radius for obj in visible])[:, None]
    lo = (centers - radii).min(axis=0); hi = (centers + radii).max(axis=0)
    points = sample_points(lo, hi, (7, 7, 7))
    frame = context.scene.frame_current + context.scene.frame_subframe
    fps = solver_test._scene_fps(context)
    flow = velocity(points, sources, frame / fps)
    state, _active = solver_test._force_state(context, wind_frame=frame)
    # GAIA applies this modulation only to the legacy scene-wide Wind vector.
    starts = [obj.cloth_next.bake_start for obj in context.scene.objects
              if hasattr(obj, 'cloth_next') and obj.cloth_next.enabled
              and obj.cloth_next.role in {'CLOTH', 'SOFT_BODY', 'ROD', 'RIGID_BODY'}]
    seconds = max(0., frame - min(starts, default=context.scene.frame_start)) / fps
    flow += np.asarray(state.wind) * (.875 + .125 * math.sin(30 * seconds))
    scale = visible[0].cloth_next.force.wind_vector_scale
    spacing = float(np.min(hi - lo) / 6)
    return arrow_lines(points, flow, scale, min(1., spacing * .8))


def redraw(_self=None, context=None):
    context = context or bpy.context
    for window in getattr(context.window_manager, 'windows', ()):
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def draw(region=None, context=None):
    context = context or bpy.context
    if getattr(getattr(context.space_data, 'overlay', None), 'show_overlays', True) is False:
        return
    region = region or context.region_data
    if region is None:
        return
    vertices = preview(context)
    if not len(vertices):
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    blend, depth, width = gpu.state.blend_get(), gpu.state.depth_test_get(), gpu.state.line_width_get()
    try:
        with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
            gpu.matrix.load_matrix(region.view_matrix)
            gpu.matrix.load_projection_matrix(region.window_matrix)
            gpu.state.blend_set('ALPHA'); gpu.state.depth_test_set('NONE')
            gpu.state.line_width_set(2.)
            shader.bind(); shader.uniform_float('color', (.15, .8, 1., .85))
            batch_for_shader(shader, 'LINES', {'pos': vertices}).draw(shader)
    finally:
        gpu.state.blend_set(blend); gpu.state.depth_test_set(depth); gpu.state.line_width_set(width)


def _draw():
    try:
        draw()
    except (ValueError, AttributeError, ReferenceError, RuntimeError):
        return


def _load_post(_unused):
    _remove_handler()
    _ensure_handler()


_load_post = getattr(bpy.app.handlers, 'persistent', lambda f: f)(_load_post)
_load_post._clothnext_wind_overlay = True


def _frame_change_post(_scene, _depsgraph=None):
    redraw()


_frame_change_post = getattr(bpy.app.handlers, 'persistent', lambda f: f)(_frame_change_post)
_frame_change_post._clothnext_wind_redraw = True


def _remove_handler():
    global _handle
    if _handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        except (ValueError, RuntimeError):
            pass
        _handle = None


def _ensure_handler():
    global _handle
    if _handle is None and not getattr(bpy.app, 'background', False) and hasattr(bpy.types, 'SpaceView3D'):
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


def register():
    for handler in tuple(bpy.app.handlers.load_post):
        if getattr(handler, '_clothnext_wind_overlay', False):
            if handler is not _load_post:
                handler.__globals__['unregister']()
            else:
                bpy.app.handlers.load_post.remove(handler)
    bpy.app.handlers.load_post.append(_load_post)
    for handler in tuple(bpy.app.handlers.frame_change_post):
        if getattr(handler, '_clothnext_wind_redraw', False):
            bpy.app.handlers.frame_change_post.remove(handler)
    bpy.app.handlers.frame_change_post.append(_frame_change_post)
    _ensure_handler()


def unregister():
    _remove_handler()
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    if _frame_change_post in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_frame_change_post)
