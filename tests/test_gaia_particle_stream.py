import struct
import numpy as np
import pytest
from cloth_next.gaia.particle_stream import ParticleFileSample, vector_layout
from cloth_next.gaia.water_field import reconstruct, reconstruct_chunks


def cache(path, values, counts=None):
    counts=counts or (len(values),0,0)
    path.write_bytes(struct.pack('<4I',*counts,1)+struct.pack('<3I',*counts)
        +np.asarray(values,dtype='<f4').tobytes())
    return path


def test_all_particles_streamed_and_same_reconstruction(tmp_path):
    rng=np.random.default_rng(4)
    p=rng.uniform(.1,.9,(65539,3)).astype('f4')
    v=rng.normal(size=p.shape).astype('f4')*300
    positions=cache(tmp_path/'p.ffp3',p,(20000,20000,25539))
    velocities=cache(tmp_path/'v.ffp3',v,(20000,20000,25539))
    sample=ParticleFileSample(positions,velocities,tuple(map(tuple,np.eye(4))),(0,0,0))
    chunks=list(sample.chunks())
    assert [len(x) for x,y in chunks]==[65536,3]
    full=reconstruct(p,v,(0,0,0),(1,1,1),(8,8,8))
    streamed=reconstruct_chunks(sample.chunks(),(0,0,0),(1,1,1),(8,8,8))
    np.testing.assert_array_equal(full.velocity,streamed.velocity)
    np.testing.assert_array_equal(full.influence,streamed.influence)
    assert streamed.source_particle_count==65539


def test_loader_translation_world_transform_and_physical_velocity(tmp_path):
    p=np.array([[2,3,4],[5,6,7]],dtype='f4');v=p*4
    matrix=np.array([[0,-2,0,10],[3,0,0,20],[0,0,4,30],[0,0,0,1]])
    sample=ParticleFileSample(cache(tmp_path/'p',p),cache(tmp_path/'v',v),
        tuple(map(tuple,matrix)),(1,2,3))
    positions,velocities=next(sample.chunks())
    np.testing.assert_array_equal(positions,(p-np.array([1,2,3],dtype='f4'))@matrix[:3,:3].T+matrix[:3,3])
    np.testing.assert_array_equal(velocities,v)


def test_truncation_and_mismatched_partition_rejected(tmp_path):
    path=tmp_path/'p';path.write_bytes(b'bad')
    with pytest.raises(ValueError,match='Truncated'):vector_layout(path)
    p=cache(path,np.zeros((2,3)))
    v=cache(tmp_path/'v',np.zeros((2,3)),(0,2,0))
    with pytest.raises(ValueError,match='layouts differ'):
        list(ParticleFileSample(p,v,tuple(map(tuple,np.eye(4))),(0,0,0)).chunks())


def test_cancel_between_chunks_and_nonfinite_in_late_chunk(tmp_path):
    p=np.zeros((65537,3),dtype='f4');v=p.copy();v[-1,0]=np.inf
    sample=ParticleFileSample(cache(tmp_path/'p',p),cache(tmp_path/'v',v),tuple(map(tuple,np.eye(4))),(0,0,0))
    with pytest.raises(ValueError,match='Invalid'):
        reconstruct_chunks(sample.chunks(),(0,0,0),(1,1,1),(4,4,4))
    calls=[]
    def cancel():
        calls.append(1)
        if len(calls)==2:raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):list(sample.chunks(cancel))


def test_millions_of_particles_do_not_require_a_resident_frame():
    # The original full-frame estimate exceeds 512 MiB for this population.
    p=np.full((65536,3),.5);v=np.ones_like(p)
    field=reconstruct_chunks(((p,v) for _ in range(128)),(0,0,0),(1,1,1),(2,2,2),max_speed=None)
    assert field.source_particle_count==8388608
    np.testing.assert_array_equal(field.velocity,np.ones((2,2,2,3),dtype='f4'))
    np.testing.assert_array_equal(field.influence,np.ones((2,2,2),dtype='f4'))
