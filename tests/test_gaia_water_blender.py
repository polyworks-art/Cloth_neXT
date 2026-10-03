import sys
from types import SimpleNamespace
import numpy as np
import pytest
from cloth_next.gaia.container import write_water_container
from cloth_next.gaia.water_field import reconstruct, debug_vectors
from cloth_next.ppf.schema import envelope


def test_disabled_fast_path_does_not_read_cache(blender_env, monkeypatch):
    module=sys.modules['cloth_next.blender.water_flow']
    monkeypatch.setattr(module,'prepare_container',lambda *a:pytest.fail('disabled path read FLIP'))
    context=SimpleNamespace(scene=SimpleNamespace(objects=[]))
    payload=object()
    assert module.apply_water_payload(context,payload,None,1,10)==(payload,None)


def test_enabled_official_payload_and_targeting(blender_env, monkeypatch, tmp_path):
    module=sys.modules['cloth_next.blender.water_flow']
    f=reconstruct([[.5,.5,.5]],[[2,0,0]],[0,0,0],[1,1,1],[3,3,3])
    p=tmp_path/'flow.gaia'
    write_water_container(p,{},[(1,0,f),(2,1,f)])
    monkeypatch.setattr(module,'prepare_container',lambda *a:p)
    settings=SimpleNamespace(enabled=True,role='CLOTH',water_flow_enabled=True,
                             water_flow_influence=1.,water_flow_velocity_scale=1.)
    context=SimpleNamespace(scene=SimpleNamespace(objects=[SimpleNamespace(name='Cloth',cloth_next=settings)]))
    payload=envelope.dumps_envelope(envelope.KIND_PARAM,{'scene':{'air-density':.001},
        'group':[[{},['Cloth'],['uuid']],[{},['Other'],['other']]]},schema_version=2)
    resolved=SimpleNamespace(protocol_version='0.23')
    result,digest=module.apply_water_payload(context,payload,resolved,1,2)
    tree=envelope.loads_envelope(result,envelope.KIND_PARAM,schema_version=2)
    assert tree['force_field']['grids'][0]['groups']==[0]
    assert tree['group'][0][0]['force-field-weight']==1
    assert 'force-field-weight' not in tree['group'][1][0]
    assert len(digest)==64
    with pytest.raises(ValueError,match='0.23'):
        module.apply_water_payload(context,payload,SimpleNamespace(protocol_version='0.22'),1,2)


def test_debug_vectors_match_encoded_field():
    f=reconstruct([[.5,.5,.5]],[[2,0,0]],[0,0,0],[1,1,1],[3,3,3])
    a,b=debug_vectors(f,1,.25)
    assert len(a)==1
    np.testing.assert_allclose(a,[[.5,.5,.5]])
    np.testing.assert_allclose(b-a,[[.5,0,0]])


def test_new_water_container_filename_gets_extension(blender_env, monkeypatch, tmp_path):
    module = sys.modules['cloth_next.blender.water_flow']
    monkeypatch.setattr(module.bpy.path, 'abspath', lambda value: value)
    destination = module.water_container_destination(str(tmp_path / 'NewFlow'), 'Plane')
    assert destination == tmp_path / 'NewFlow.gaia'
    assert not destination.exists()


def test_water_output_folder_creates_new_object_filename(blender_env, monkeypatch, tmp_path):
    module = sys.modules['cloth_next.blender.water_flow']
    monkeypatch.setattr(module.bpy.path, 'abspath', lambda value: value)
    assert module.water_container_destination(str(tmp_path), 'Cloth / A') == tmp_path / 'Cloth___A_WaterFlow.gaia'


def test_water_destination_never_overwrites_selected_source_file(blender_env, monkeypatch, tmp_path):
    module = sys.modules['cloth_next.blender.water_flow']
    monkeypatch.setattr(module.bpy.path, 'abspath', lambda value: value)
    source = tmp_path / 'Watertest.blend'
    source.write_bytes(b'original scene')
    assert module.water_container_destination(str(source), 'Plane') == tmp_path / 'Watertest.gaia'
    assert source.read_bytes() == b'original scene'


def test_authoritative_flip_conversion_separates_points_and_world_velocity(blender_env):
    module=sys.modules['cloth_next.blender.water_flow']
    matrix=np.asarray([[0,-2,0,10],[2,0,0,20],[0,0,3,30],[0,0,0,1.]])
    p,v=module.flip_sample_to_world([[1,2,3]],[[2,3,4]],matrix)
    np.testing.assert_allclose(p,[[6,22,39]])
    np.testing.assert_array_equal(v,[[2,3,4]])
    with pytest.raises(ValueError,match='Non-finite'):
        module.flip_sample_to_world([[np.nan,0,0]],[[1,0,0]],matrix)


@pytest.mark.parametrize('mode', ['POSITIONS', 'CONSTANT_X', 'DIRECTION', 'MAGNITUDE'])
def test_overlay_upload_uses_float32_world_coordinates(blender_env, monkeypatch, tmp_path, mode):
    from contextlib import nullcontext
    module = sys.modules['cloth_next.blender.water_flow']
    field = reconstruct([[.5,.5,.5]], [[2,0,0]], [0,0,0], [1,1,1], [3,3,3])
    path = tmp_path / 'preview.gaia'
    write_water_container(path, {}, [(1,0,field)])
    settings = SimpleNamespace(water_flow_enabled=True, water_flow_show_vectors=True,
        water_flow_container=str(path), water_flow_vector_stride=1,
        water_flow_vector_scale=.2, water_flow_influence=1., water_flow_velocity_scale=1.,
        water_flow_debug_mode=mode, water_flow_show_bounds=True)
    monkeypatch.setattr(module.bpy, 'context', SimpleNamespace(
        object=SimpleNamespace(name='Cloth',cloth_next=settings), scene=SimpleNamespace(frame_current=1)))
    monkeypatch.setattr(module.bpy.path, 'abspath', lambda value: value)
    uploads = []
    shader = SimpleNamespace(bind=lambda: None, uniform_float=lambda *args: None)
    gpu = SimpleNamespace(shader=SimpleNamespace(from_builtin=lambda name: shader),
        matrix=SimpleNamespace(push_pop=nullcontext, push_pop_projection=nullcontext,
            load_matrix=lambda matrix: None, load_projection_matrix=lambda matrix: None),
        state=SimpleNamespace(point_size_set=lambda value: None))
    def batch(_shader, primitive, attributes):
        values = attributes['pos']
        assert values.dtype == np.float32 and values.flags.c_contiguous
        uploads.append((primitive, values.copy()))
        return SimpleNamespace(draw=lambda shader: None)
    monkeypatch.setitem(sys.modules, 'gpu', gpu)
    monkeypatch.setitem(sys.modules, 'gpu_extras.batch', SimpleNamespace(batch_for_shader=batch))
    module._preview.clear()
    module._draw_vectors(_region=SimpleNamespace(view_matrix=np.eye(4), window_matrix=np.eye(4)))
    assert len(uploads) == 2
    assert uploads[0][0] == ('POINTS' if mode == 'POSITIONS' else 'LINES')
    np.testing.assert_allclose(uploads[0][1][0], [.5,.5,.5])
    if mode != 'POSITIONS':
        np.testing.assert_allclose(uploads[0][1][1]-uploads[0][1][0], [.2,0,0] if mode != 'MAGNITUDE' else [.4,0,0], atol=1e-7)


def test_streaming_disabled_fast_path_has_no_cache_or_solver_dependency(blender_env, monkeypatch):
    module = sys.modules['cloth_next.blender.water_flow']
    monkeypatch.setattr(module, 'prepare_container', lambda *a: pytest.fail('disabled streaming read cache'))
    context = SimpleNamespace(scene=SimpleNamespace(objects=[]))
    payload = object()
    assert module.apply_water_stream_payload(context, payload, None, 120, 200) == (payload, None, '')


def test_production_exports_cache_identity_and_weights_without_schedule(blender_env, monkeypatch, tmp_path):
    from cloth_next.gaia.water_field import fingerprint
    from cloth_next.ppf.models import ConnectionOwnership
    module = sys.modules['cloth_next.blender.water_flow']
    field = reconstruct([[.5, .5, .5]], [[2, 0, 0]], [0, 0, 0], [1, 1, 1], [3, 3, 3])
    metadata = {'fps': 24., 'dimensions': [3, 3, 3], 'field_format': 'gaia-0.23',
                'physical_velocity_units': 'm/s'}
    metadata['fingerprint'] = fingerprint(metadata)
    path = tmp_path / 'prepared.gaia'
    write_water_container(path, metadata, [(120, 0., field), (121, 1 / 24., field)])
    calls = []
    monkeypatch.setattr(module, 'prepare_container', lambda *a: (calls.append(a), path)[1])
    settings = SimpleNamespace(enabled=True, role='CLOTH', water_flow_enabled=True,
                               water_flow_influence=.5, water_flow_velocity_scale=2.)
    context = SimpleNamespace(scene=SimpleNamespace(
        objects=[SimpleNamespace(name='Cloth', cloth_next=settings)],
        render=SimpleNamespace(fps=24, fps_base=1)))
    payload = envelope.dumps_envelope(envelope.KIND_PARAM, {
        'scene': {'air-density': .001}, 'group': [[{}, ['Cloth'], ['uuid']]]}, schema_version=2)
    resolved = SimpleNamespace(protocol_version='0.23', ownership=ConnectionOwnership.OWNED_PROCESS)
    data, digest, descriptor = module.apply_water_stream_payload(context, payload, resolved, 120, 121)
    tree = envelope.loads_envelope(data, envelope.KIND_PARAM, schema_version=2)
    import json
    document = json.loads(descriptor)
    assert 'force_field' not in tree
    assert tree['group'][0][0]['force-field-weight'] == 1.
    assert document['first'] == 120 and document['last'] == 121
    assert document['targets'][0]['fingerprint'] == metadata['fingerprint']
    assert document['targets'][0]['groups'] == [0] and len(calls) == 1
    assert len(digest) == 64
