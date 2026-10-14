# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest
from cloth_next.gaia.native_fluid import pack_frame, chunks, native_fluid_payload, AXES
from cloth_next.gaia.water_field import WaterVelocityField, fingerprint
from cloth_next.gaia.container import GaiaContainer, write_water_container


def field(velocity=(2.,3.,4.), occupancy=1., shape=(2,2,2)):
    return WaterVelocityField((0.,0.,0.),(1.,2.,3.),
        np.broadcast_to(np.asarray(velocity,dtype='<f4'),shape+(3,)).copy(),
        np.full(shape,occupancy,dtype='<f4'))


def cache(tmp_path, *, fps=24., frames=(7,8), mode='FLUID_PARTICLES', units='m/s', sample=None):
    metadata={'provider':{'mode':mode},'dimensions':[2,2,2], 'fps':fps,
              'velocity_policy':'unclamped',
              'field_format':'gaia-0.23','physical_velocity_units':units}
    metadata['fingerprint']=fingerprint(metadata)
    path=tmp_path/'flow.gaia'
    write_water_container(path,metadata,[(f,(f-7)/fps,sample or field()) for f in frames])
    return path


def payload(path, **kwargs):
    with GaiaContainer(path) as c:
        return native_fluid_payload(c,[0],first=7,last=8,fps=24.,**kwargs)


def test_strength_never_changes_source_velocity(tmp_path):
    path=cache(tmp_path)
    a,b=payload(path,strength=1.),payload(path,strength=10.)
    assert a['fields'][0]['fluid']==b['fields'][0]['fluid']
    assert 'grids' not in a
    assert a['fields'][0]['flow']==1.
    assert a['fields'][0]['use_density'] is True


def test_still_water_is_kept_and_empty_water_is_absent():
    t,b=pack_frame(field((0.,0.,0.),1.))
    assert t.tolist()==[0]
    assert np.count_nonzero(b[...,3])==8
    assert not np.any(b[...,:3])
    t,b=pack_frame(field((2.,0.,0.),0.))
    assert t.tolist()==[0xffffffff] and b.shape==(0,512,4)
    assert chunks(b)==[]


def test_brick_order_x_fastest_and_boundary_padding():
    f=field(shape=(9,3,10))
    f.velocity[...,0]=np.arange(10)[None,None,:]
    t,b=pack_frame(f)
    assert t.tolist()==[0,1,2,3]
    np.testing.assert_array_equal(b[0,:8,0],np.arange(8))
    assert b[1,0,0]==8 and b[1,1,0]==9 and b[1,2,3]==0
    assert np.count_nonzero(b[...,3])==270


def test_coordinate_node_centers_and_time_rate(tmp_path):
    p=payload(cache(tmp_path),solver_fps=48.)['fields'][0]['fluid']
    assert p['cell']==[[.5,1.,1.5]]*2
    assert p['corner']==[[0.,0.,0.]]*2
    assert p['axes']==[AXES]*2
    assert p['step']==1/48
    b=np.frombuffer(b''.join(p['bricks'][0]),dtype='<f4').reshape(-1,512,4)
    np.testing.assert_array_equal(b[0,0],[4.,6.,8.,1.])
    corner=np.asarray(p['corner'][0]); axes=np.asarray(AXES)
    # The first cell center stays inside the original bounds, without a halo.
    np.testing.assert_allclose(corner+axes@(np.asarray(p['cell'][0])*.5),[.25,.75,-.5])


def test_native_centers_sample_source_data_without_expanding_liquid_bounds():
    f=field()
    f.velocity[...,0]=[0.,8.]
    _,bricks=pack_frame(f,cell_centers=True)
    np.testing.assert_allclose(bricks[0,:2,0],[2.,6.])


@pytest.mark.parametrize('scale',[None,0.,2.,float('nan')])
def test_uncertified_baked_world_scale_refused(tmp_path,scale):
    path=cache(tmp_path)
    with GaiaContainer(path) as c:
        c.manifest['water']['provider'].update(cache='source',baked_world_scale=scale)
        with pytest.raises(ValueError,match='World Scale 1'):
            native_fluid_payload(c,[0],first=7,last=8,fps=24.)


def test_one_mib_chunks():
    a=np.zeros(800000,dtype='<f4')
    c=chunks(a)
    assert len(c)==4 and all(type(x) is bytes and len(x)<=1048576 for x in c)
    assert sum(map(len,c))==a.nbytes


@pytest.mark.parametrize('kwargs,match',[
    ({'mode':'SURFACE_VELOCITY'},'surface-only'),
    ({'units':'cm/s'},'m/s'),
    ({'frames':(7,9)},'missing'),
    ({'fps':30.},'FPS'),
])
def test_invalid_source_refused(tmp_path,kwargs,match):
    with pytest.raises(ValueError,match=match):payload(cache(tmp_path,**kwargs))


@pytest.mark.parametrize('kwargs',[
    {'strength':-1.},{'strength':float('nan')},{'flow':2.},{'solver_fps':0.}])
def test_bad_parameters_refused(tmp_path,kwargs):
    with pytest.raises(ValueError):payload(cache(tmp_path),**kwargs)


def test_budget_is_checked_before_next_allocation(tmp_path):
    with pytest.raises(ValueError,match='budget'):payload(cache(tmp_path),budget=100)


def test_native_dispatch_and_air_density_independence(blender_env,monkeypatch,tmp_path):
    import json
    import sys
    from types import SimpleNamespace as NS
    from cloth_next.ppf.schema import envelope
    from cloth_next.ppf.models import ConnectionOwnership
    module=sys.modules['cloth_next.blender.water_flow']
    path=cache(tmp_path)
    monkeypatch.setattr(module,'prepare_container',lambda *a:path)
    s=NS(enabled=True,role='CLOTH',water_flow_enabled=True,water_flow_influence=.5,
         water_flow_velocity_scale=1.,water_flow_coupling_strength=6.,water_flow_native_flow=1.)
    context=NS(scene=NS(objects=[NS(name='Cloth',cloth_next=s)],render=NS(fps=24,fps_base=1.)))
    raw=envelope.dumps_envelope(envelope.KIND_PARAM,{'scene':{'fps':24.,'air-density':0.},
        'group':[[{},['Cloth'],['id']]]},schema_version=2)
    resolved=NS(protocol_version='0.26',schema_version='2',ownership=ConnectionOwnership.OWNED_PROCESS)
    data,digest,stream=module.apply_water_stream_payload(context,raw,resolved,7,8)
    decoded=envelope.loads_envelope(data,envelope.KIND_PARAM,schema_version=2)
    assert decoded['force_field']['fields']==[]
    descriptor=json.loads(stream)
    assert descriptor['field_kind']=='native-file'
    assert descriptor['targets'][0]['strength']==6.
    assert descriptor['targets'][0]['influence']==.5
    assert len(digest)==64
    s.water_flow_velocity_scale=2.
    with pytest.raises(ValueError,match='Velocity Scale 1'):
        module.apply_water_stream_payload(context,raw,resolved,7,8)


def test_native_held_window_uses_run_clock_and_preserves_density(tmp_path):
    from cloth_next.gaia.native_fluid import NativeFluidStreamReader
    path=cache(tmp_path,frames=(7,8,9))
    with GaiaContainer(path) as c: digest=c.manifest['water']['fingerprint']
    with NativeFluidStreamReader(path,first=7,last=9,fps=24.,solver_fps=48.,
            expected_fingerprint=digest,strength=6.,flow=.25) as reader:
        window=reader.window(1,influence=.5)
        assert window.source_frames==(8,9)
        assert window.times==(1/48,2/48)
        assert window.volume['start']==1/48 and window.volume['step']==1/48
        assert window.strength==3. and window.flow==.25
        np.testing.assert_array_equal(window.volume['bricks'][0][0,0],(4.,6.,8.,1.))
        assert reader.window(2).source_frames==(9,)
        with pytest.raises(ValueError): reader.window(3)
        with pytest.raises(ValueError): reader.window(0,velocity_scale=2.)


def test_native_held_memory_is_independent_of_sequence_length(tmp_path):
    from cloth_next.gaia.native_fluid import NativeFluidStreamReader
    path=cache(tmp_path,frames=tuple(range(7,257)))
    with GaiaContainer(path) as c: digest=c.manifest['water']['fingerprint']
    with NativeFluidStreamReader(path,first=7,last=256,fps=24.,
            expected_fingerprint=digest) as reader:
        first=reader.window(0)
        last=reader.window(247)
        assert len(first.volume['tables'])==len(last.volume['tables'])==3
        assert first.packed_bytes==last.packed_bytes
        assert last.source_frames==(254,255,256)
        assert reader.window(248).source_frames==(255,256)
        assert reader.active_bytes_bound<128*1024*1024


def test_native_capability_does_not_enable_old_profiles():
    from cloth_next.ppf.compatibility import protocol_profile, validate_versions
    from cloth_next.ppf.adapters import ADAPTERS
    assert ADAPTERS[protocol_profile('0.26','2').adapter_id].native_fluid
    for version in ('0.13','0.18','0.22','0.23'):
        assert not ADAPTERS[protocol_profile(version,'2').adapter_id].native_fluid
    assert not validate_versions('0.25','2','0.1.0').fully_compatible
    assert not validate_versions('0.26','1','0.1.0').fully_compatible


def test_native_overlay_is_inspection_only_and_fails_closed(tmp_path):
    from cloth_next.ppf.solver_overlay import apply_solver_overlay, INTEGRATION_RECIPES, SolverOverlayError
    recipe=INTEGRATION_RECIPES['gaia-026-official']
    root=tmp_path/'frontend';root.mkdir()
    (root/'_scene_.py').write_text('\n'.join(recipe.anchors))
    (root/'_force_field_.py').write_text('\n'.join(recipe.field_anchors))
    (root/'build_worker.py').write_text('unchanged official worker')
    before={p.name:p.read_bytes() for p in root.iterdir()}
    args={'protocol_version':'0.26','schema_version':'2',
          'official_release_tag':'2026-10-13-12-32','managed':True}
    apply_solver_overlay(tmp_path,**args)
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}
    assert list(tmp_path.iterdir())==[root]
    (root/'_force_field_.py').write_text('old grids API')
    with pytest.raises(SolverOverlayError,match='fluid API'):apply_solver_overlay(tmp_path,**args)


def test_unclamped_reconstruction_preserves_large_real_source_velocities():
    from cloth_next.gaia.water_field import reconstruct
    result=reconstruct([[.5,.5,.5]],[[200.,0.,0.]],[0,0,0],[1,1,1],[3,3,3],max_speed=None)
    assert result.velocity[1,1,1,0]==200.


@pytest.mark.parametrize('occupancy',[0.,1.])
def test_file_payload_matches_full_native_wire(tmp_path,occupancy):
    from cloth_next.gaia.native_fluid import native_fluid_payload_file
    from cloth_next.ppf.schema import envelope
    import hashlib
    path=cache(tmp_path,sample=field(occupancy=occupancy))
    with GaiaContainer(path) as container:
        digest=container.manifest['water']['fingerprint']
    base={'scene':{'fps':24.},'force_field':{'fields':[],'scripts':[]}}
    raw=envelope.dumps_envelope(envelope.KIND_PARAM,base,schema_version=2)
    descriptor={'first':7,'last':8,'fps':24.,'solver_fps':24.,'targets':[
        {'path':str(path),'fingerprint':digest,'groups':[0],'strength':2.,'influence':.5,'flow':1.}]}
    output=tmp_path/'fresh-worker'/'native.cbor'
    result=native_fluid_payload_file(raw,descriptor,output)
    base['force_field']=payload(path,strength=1.)
    expected=envelope.dumps_envelope(envelope.KIND_PARAM,base,schema_version=2)
    assert output.read_bytes()==expected
    assert result==hashlib.sha256(expected).hexdigest()
    assert not list(output.parent.glob('native-fluid-spool-*'))


def test_file_payload_cancel_cleans_spool(tmp_path):
    from cloth_next.gaia.native_fluid import native_fluid_payload_file
    from cloth_next.ppf.schema import envelope
    path=cache(tmp_path)
    with GaiaContainer(path) as container: digest=container.manifest['water']['fingerprint']
    descriptor={'first':7,'last':8,'fps':24.,'solver_fps':24.,'targets':[
        {'path':str(path),'fingerprint':digest,'groups':[0],'strength':1.,'influence':1.,'flow':1.}]}
    def cancel(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        native_fluid_payload_file(envelope.dumps_envelope(envelope.KIND_PARAM,{},schema_version=2),
                                  descriptor,tmp_path/'native.cbor',check_cancel=cancel)
    assert not list(tmp_path.glob('native-fluid-spool-*'))
    assert not (tmp_path/'native.cbor').exists()



def test_file_payload_refuses_inconsistent_frame_dimensions(tmp_path):
    from cloth_next.gaia.native_fluid import native_fluid_payload_file
    from cloth_next.ppf.schema import envelope
    path=cache(tmp_path,sample=field(shape=(3,2,2)))
    with GaiaContainer(path) as container: digest=container.manifest['water']['fingerprint']
    descriptor={'first':7,'last':8,'fps':24.,'solver_fps':24.,'targets':[
        {'path':str(path),'fingerprint':digest,'groups':[0],'strength':1.,'influence':1.,'flow':1.}]}
    output=tmp_path/'native.cbor'
    with pytest.raises(ValueError,match='incompatible dimensions'):
        native_fluid_payload_file(envelope.dumps_envelope(envelope.KIND_PARAM,{},schema_version=2),
                                  descriptor,output)
    assert not output.exists()
    assert not list(tmp_path.glob('native-fluid-spool-*'))
    with GaiaContainer(path) as container: assert container.frame(0).velocity.shape==(3,2,2,3)
