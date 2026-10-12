# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender timeline/preview/export regression, no solver prerequisite."""
import json
import math
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace
import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.blender import registration, wind_fields, solver_test
from cloth_next.ppf.schema import envelope

registration.register()
try:
    preview_object = bpy.data.objects.get('Cube')
    preview_object.scale = (.15,.15,.15)
    preview_object.location.x = .3
    wind_fields.register(); wind_fields.register()
    assert bpy.app.handlers.frame_change_post.count(wind_fields._frame_change_post) == 1
    assert bpy.app.handlers.load_post.count(wind_fields._load_post) == 1
    bpy.ops.object.empty_add()
    obj = bpy.context.object
    obj.cloth_next.enabled = True; obj.cloth_next.role = 'FORCE'
    force = obj.cloth_next.force
    force.gravity_strength = 0; force.wind_local = True
    force.wind_radius = 1; force.wind_resolution = 5
    force.wind_obstacles = 'NONE'; force.wind_vector_scale = .005
    force.wind_show_vectors = True; force.air_density = .1
    obj.rotation_euler.y = math.pi / 2
    force.wind_strength = 1; force.keyframe_insert('wind_strength', frame=1)
    force.wind_strength = 3; force.keyframe_insert('wind_strength', frame=5)
    obj.location.x = 0; obj.keyframe_insert('location', frame=1)
    obj.location.x = .2; obj.keyframe_insert('location', frame=5)
    scene = bpy.context.scene
    scene.frame_set(1); before = wind_fields.preview(bpy.context)
    first = wind_fields.source_at(bpy.context, obj)
    scene.frame_set(5); after = wind_fields.preview(bpy.context)
    last = wind_fields.source_at(bpy.context, obj)
    assert first.speed == 1 and last.speed == 3
    assert abs(first.center[0]) < 1e-6 and abs(last.center[0] - .2) < 1e-6
    assert before.dtype == np.float32 and after.dtype == np.float32
    assert not np.allclose(before, after)
    state, _ = solver_test._force_state(bpy.context, wind_frame=5)
    assert state.wind == (0, 0, 0), 'Local Wind must not also become global Wind'
    scene.frame_set(3, subframe=.25)
    tree = {'scene': {'air-density': .1}, 'group': [[{}, ['Cloth'], ['cloth']]],
            'force_field': {'grids': [], 'scripts': []}}
    payload = envelope.dumps_envelope(envelope.KIND_PARAM, tree, schema_version=2)
    encoded, digest = wind_fields.apply_wind_payload(
        bpy.context, payload, SimpleNamespace(protocol_version='0.23'), 1, 5)
    assert scene.frame_current == 3 and scene.frame_subframe == .25
    result = envelope.loads_envelope(encoded, envelope.KIND_PARAM, schema_version=2)
    grid = result['force_field']['grids'][0]
    data = np.frombuffer(zlib.decompress(grid['data']), '<f4').reshape(grid['shape'])
    assert grid['times'][0] == 0 and grid['times'][-1] == 4 / solver_test._scene_fps(bpy.context)
    assert np.isfinite(data).all() and data[-1,...,0].max() > data[0,...,0].max()
    assert result['group'][0][0]['force-field-weight'] == 1
    saved = ROOT / '.tmp_verify'; saved.mkdir(exist_ok=True)
    (saved / 'wind-field-smoke-param.cbor').write_bytes(encoded)
    (saved / 'wind-field-smoke.json').write_text(json.dumps({
        'result':'PASS', 'first_speed':first.speed, 'last_speed':last.speed,
        'shape':grid['shape'], 'digest':digest, 'preview_vertices':len(after)}, indent=2))
    force.wind_turbulence = 1
    scene.frame_set(1); a = wind_fields.preview(bpy.context)
    scene.frame_set(2); b = wind_fields.preview(bpy.context)
    assert not np.allclose(a, b)
    # Display controls alone never touch the force input.
    force.wind_show_vectors = False
    assert len(wind_fields.preview(bpy.context)) == 0
    assert wind_fields.source_at(bpy.context, obj).turbulence == 1
    try:
        wind_fields.apply_wind_payload(bpy.context, payload, SimpleNamespace(protocol_version='0.22'), 1, 5)
    except solver_test.SceneValidationError:
        pass
    else:
        raise AssertionError('Unsupported solver accepted Local Wind')
    assert scene.frame_current == 2
    scene.frame_set(3, subframe=.25)
    budget = wind_fields.MAX_SCHEDULE_BYTES
    wind_fields.MAX_SCHEDULE_BYTES = 1
    try:
        try:
            wind_fields.apply_wind_payload(bpy.context, payload, SimpleNamespace(protocol_version='0.23'), 1, 5)
        except solver_test.SceneValidationError as exc:
            assert '32 MiB' in str(exc)
        else:
            raise AssertionError('Wind upload exceeded the budget')
    finally:
        wind_fields.MAX_SCHEDULE_BYTES = budget
    assert scene.frame_current == 3 and scene.frame_subframe == .25
    check = solver_test._check_preparation_cancel
    calls = [0]
    def cancel_after_first_sample():
        calls[0] += 1
        if calls[0] == 2:
            raise solver_test.SessionCancelled()
    solver_test._check_preparation_cancel = cancel_after_first_sample
    try:
        try:
            wind_fields.apply_wind_payload(bpy.context, payload, SimpleNamespace(protocol_version='0.23'), 1, 5)
        except solver_test.SessionCancelled:
            pass
        else:
            raise AssertionError('Wind preparation ignored Cancel')
    finally:
        solver_test._check_preparation_cancel = check
    assert scene.frame_current == 3 and scene.frame_subframe == .25
    force.wind_local = False
    unchanged, digest = wind_fields.apply_wind_payload(bpy.context, payload, SimpleNamespace(protocol_version='0.22'), 1, 5)
    assert unchanged is payload and digest is None
    print('CLOTH_NEXT_WIND_FIELD_SMOKE_PASS')
finally:
    registration.unregister()
    assert wind_fields._handle is None
    assert wind_fields._load_post not in bpy.app.handlers.load_post
    assert wind_fields._frame_change_post not in bpy.app.handlers.frame_change_post
