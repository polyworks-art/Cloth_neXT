import numpy as np
import pytest
from cloth_next.gaia.water_field import reconstruct_chunks
from tools.benchmark_gaia_reconstruction import reference_reconstruct


@pytest.mark.parametrize('max_speed',[None,100.])
def test_optimized_splat_matches_original_order_at_boundaries_and_chunk_edge(max_speed):
    rng=np.random.default_rng(19)
    p=rng.uniform(-.2,1.2,(65539,3)).astype('f4')
    p[:4]=[[0,0,0],[1,1,1],[.5,.5,.5],[0,1,.5]]
    v=rng.normal(size=p.shape)*200
    def chunks():
        yield p[:65536],v[:65536]
        yield p[65536:],v[65536:]
    options=dict(max_speed=max_speed,support_threshold=1000.)
    expected=reference_reconstruct(chunks(),(0,0,0),(1,1,1),(5,7,9),**options)
    actual=reconstruct_chunks(chunks(),(0,0,0),(1,1,1),(5,7,9),**options)
    np.testing.assert_array_equal(expected.velocity,actual.velocity)
    np.testing.assert_array_equal(expected.influence,actual.influence)
    assert expected.source_particle_count==actual.source_particle_count==65539
