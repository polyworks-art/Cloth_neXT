import json
import tomllib
import numpy as np
import pytest
from cloth_next.gaia.hydrodynamic_transport import ExperimentalHydroWriter,dense_bricks,ROLES


def test_typed_surface_cannot_be_published_with_missing_velocity_frame(tmp_path):
    writer=ExperimentalHydroWriter(tmp_path/'hydro',start=0,step=1/30,provenance={})
    with pytest.raises(ValueError,match='frames must match'):writer.finish()
    assert not (writer.path/'hydrodynamics.json').exists()


def test_sdf_sign_survives_shared_brick_storage_across_boundary(tmp_path):
    a=np.zeros((2,3,17,4));a[...,0]=np.arange(17)-8.;a[...,3]=1.
    table,bricks,res=dense_bricks(a)
    assert res==[17,3,2]
    assert bricks[table[0],7,0]==-1.
    assert bricks[table[1],0,0]==0.
    assert bricks[table[1],1,0]==1.
    writer=ExperimentalHydroWriter(tmp_path/'hydro',start=0,step=.1,provenance={'kind':'analytic'})
    for role in (0,1):
        for _ in range(2):writer.append(role,table,bricks,res,[0,0,0],np.eye(3),[.1]*3)
    writer.finish()
    outer=json.loads((writer.path/'hydrodynamics.json').read_text())
    assert outer['roles']==list(ROLES)
    inner=tomllib.loads((writer.path/'force_field.toml').read_text())
    assert inner['field'][0]['fluid']['step']==inner['field'][1]['fluid']['step']==.1
    assert len(inner['field'][1]['fluid']['tables'])==2
    assert np.fromfile(writer.path/'field-0-track.bin',dtype='<f4')[9]==0


def test_sdf_requires_identity_axes(tmp_path):
    w=ExperimentalHydroWriter(tmp_path/'hydro',start=0,step=.1,provenance={})
    t,b,r=dense_bricks(np.zeros((1,1,1,4)))
    with pytest.raises(ValueError,match='identity'):
        w.append(1,t,b,r,[0,0,0],-np.eye(3),[1,1,1])


@pytest.mark.parametrize('bad',[float('inf'),float('nan')])
def test_nonfinite_surface_frames_are_refused(bad):
    v=np.zeros((1,1,1,4));v[...,0]=bad
    with pytest.raises(ValueError,match='finite'):dense_bricks(v)
