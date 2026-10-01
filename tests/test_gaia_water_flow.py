import json
import zipfile
import numpy as np
import pytest
from cloth_next.gaia.water_field import reconstruct, solver_grid, fingerprint, WaterVelocityField
from cloth_next.gaia.container import write_water_container, GaiaContainer
from cloth_next.gaia.solver_fields import official_grid_payload, update_held_water


def field(speed=2):
    return reconstruct([[.5,.5,.5]], [[speed,0,0]], [0,0,0], [1,1,1], [3,3,3], support_threshold=.1)


def test_constant_velocity_preserved_and_empty_water_zero():
    f = field()
    occupied = f.influence > 0
    assert np.all(f.velocity[occupied] == [2,0,0])
    assert np.all(f.contribution[~occupied] == 0)
    assert f.influence[1,1,1] == 1


def test_deterministic_weighted_vectors_and_speed_bound():
    args = ([[.25,.5,.5],[.75,.5,.5]], [[2,1,0],[4,-1,0]], [0,0,0], [1,1,1], [3,3,3])
    a, b = reconstruct(*args), reconstruct(*args)
    np.testing.assert_array_equal(a.velocity, b.velocity)
    np.testing.assert_allclose(a.velocity[1,1,1], [3,0,0])
    assert np.linalg.norm(field(1000).velocity, axis=-1).max() <= 100


@pytest.mark.parametrize('positions,velocities', [([],[]), ([[0,0,0]],[[float('nan'),0,0]]),
    ([[2,2,2]],[[1,0,0]]), ([[0,0,0]],[[1,0]])])
def test_invalid_samples_are_errors(positions, velocities):
    with pytest.raises(ValueError): reconstruct(positions,velocities,[0,0,0],[1,1,1],[3,3,3])


def test_coordinate_grid_permutation_and_vector_translation_independence():
    v = np.zeros((2,3,4,3), np.float32)
    v[...,0], v[...,1], v[...,2] = 1,2,3
    f = WaterVelocityField((10,20,30),(14,23,32),v,np.ones((2,3,4),np.float32))
    lo, hi, values = solver_grid(f)
    assert lo == (10,30,-23) and hi == (14,32,-20)
    assert values.shape == (3,2,4,3)
    np.testing.assert_array_equal(values[0,0,0],[1,3,-2])


def test_domain_transforms_positions_but_not_physical_world_velocity():
    for rotation_scale in (np.eye(3), np.diag([2,3,4]), np.array([[0,-1,0],[1,0,0],[0,0,1]])):
        corners = np.asarray([[x,y,z] for x in (0,1) for y in (0,1) for z in (0,1)])
        corners = corners @ rotation_scale.T + [10,20,30]
        position = np.asarray([[.5,.5,.5]]) @ rotation_scale.T + [10,20,30]
        f = reconstruct(position, [[2,0,0]], corners.min(0), corners.max(0), [3,3,3])
        np.testing.assert_array_equal(f.velocity[1,1,1],[2,0,0])


def test_container_reuse_time_and_official_encoding(tmp_path):
    path = tmp_path/'water.gaia'
    write_water_container(path, {'fingerprint': fingerprint({'settings':1})}, [(1,0.,field()),(2,.5,field(4))])
    with GaiaContainer(path) as c:
        assert c.manifest['frames'][1]['time'] == .5
        result = official_grid_payload(c,[2],influence=.5)
        grid = result['grids'][0]
        assert grid['kind']=='air-velocity' and grid['groups']==[2]
        import zlib
        data = np.frombuffer(zlib.decompress(grid['data']),dtype='<f4').reshape(grid['shape'])
        assert data[0,1,1,1,0] == 1 and data[1,1,1,1,0] == 2
        np.testing.assert_array_equal(c.frame(0).contribution,field().contribution)


def test_atomic_cancel_retains_previous_container(tmp_path):
    p=tmp_path/'state.gaia'
    write_water_container(p, {}, [(1,0,field())])
    before=p.read_bytes()
    def canceled():
        yield 1,0,field()
        raise RuntimeError('cancel')
    with pytest.raises(RuntimeError): write_water_container(p,{},canceled())
    assert p.read_bytes()==before and list(tmp_path.iterdir())==[p]


def test_corrupted_member_checksum(tmp_path):
    p=tmp_path/'bad.gaia'
    write_water_container(p,{},[(1,0,field())])
    with zipfile.ZipFile(p) as z: entries={n:z.read(n) for n in z.namelist()}
    entries['water/000000/velocity.npy']=entries['water/000000/influence.npy']
    with zipfile.ZipFile(p,'w') as z:
        for n,v in entries.items(): z.writestr(n,v)
    with GaiaContainer(p) as c:
        with pytest.raises(ValueError,match='checksum'): c.frame(0)


def test_fingerprint_relevant_changes():
    assert fingerprint({'a':1,'b':2})==fingerprint({'b':2,'a':1})
    assert fingerprint({'velocity':'a'})!=fingerprint({'velocity':'b'})


def test_public_held_adapter_two_frames(tmp_path):
    p=tmp_path/'held.gaia'
    write_water_container(p,{},[(1,0,field()),(2,1,field(3)),(3,2,field(4))])
    calls=[]
    class Field:
        def clear(self): return self
        def grid(self,*args,**kwargs): calls.append((args,kwargs)); return self
    class Session:
        def update_force_field(self,f): calls.append('updated')
    with GaiaContainer(p) as c: update_held_water(Session(),Field(),c,1,['cloth'])
    assert calls[0][0][0].shape[0]==2
    assert calls[0][1]['times']==[1.,2.] and calls[-1]=='updated'


def test_memory_gate():
    with pytest.raises(ValueError,match='memory'):
        reconstruct([[.5,.5,.5]],[[1,0,0]],[0,0,0],[1,1,1],[256,256,256])


def test_debug_occupancy_points_constant_x_and_bounded_length():
    from cloth_next.gaia.water_field import debug_samples, debug_vectors
    v=np.full((3,3,3,3),100.,np.float32)
    occupancy=np.zeros((3,3,3),np.float32);occupancy[1,1,1]=1
    f=WaterVelocityField((10,20,30),(12,22,32),v,occupancy)
    p,values=debug_samples(f,1)
    np.testing.assert_array_equal(p,[[11,21,31]])
    before=f.velocity.copy()
    for mode in ('MAGNITUDE','DIRECTION','CONSTANT_X'):
        a,b=debug_vectors(f,1,10.,mode=mode,physical_scale=10.)
        np.testing.assert_array_equal(a,p)
        assert np.linalg.norm(b-a,axis=1).max()<=1.+1e-6
    a,b=debug_vectors(f,1,.2,mode='CONSTANT_X')
    np.testing.assert_allclose(b-a,[[.2,0,0]])
    # Occupied zero-velocity cells are still present in points-only mode.
    f.velocity[:]=0
    assert len(debug_samples(f,1)[0])==1
    f.velocity[:]=before
    np.testing.assert_array_equal(f.velocity,before)


def test_asymmetric_grid_every_node_has_one_axis_conversion():
    from cloth_next.ppf.coordinates import blender_position_to_ppf,blender_vector_to_ppf
    shape=(3,4,5)
    z,y,x=np.indices(shape)
    v=np.stack((x+2*y,3*y+z,4*z+x),axis=-1).astype(np.float32)
    f=WaterVelocityField((10,20,30),(14,26,34),v,np.ones(shape,np.float32))
    lo,hi,encoded=solver_grid(f)
    for zz,yy,xx in np.ndindex(shape):
        world=np.asarray(f.minimum)+(np.asarray(f.maximum)-f.minimum)*[xx,yy,zz]/[4,3,2]
        solver=np.asarray(blender_position_to_ppf(world))
        solver_index=np.rint((solver-lo)/(np.asarray(hi)-lo)*[4,2,3]).astype(int)
        np.testing.assert_array_equal(encoded[solver_index[2],solver_index[1],solver_index[0]],blender_vector_to_ppf(v[zz,yy,xx]))
