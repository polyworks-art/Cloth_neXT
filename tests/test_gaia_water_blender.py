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
