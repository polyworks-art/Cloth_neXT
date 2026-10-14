import numpy as np
import pytest
from cloth_next.gaia.water_push_prototype import limited_push,output_velocity
from cloth_next.gaia.water_push_prototype import quadratic_velocity,discrete_push


def test_estimated_velocity_increment_is_bounded_and_directed():
    rng=np.random.default_rng(81)
    water=rng.normal(size=(100,3));cloth=rng.normal(size=(100,3));support=rng.random(100)
    for response in [1.,.5,.25,.1,.05]:
        for window in [.001,.01,1/24,.1]:
            _,applied=limited_push(water,cloth,support,response,window)
            relative=water-cloth
            assert np.all(np.linalg.norm(applied*window,axis=1)<=np.linalg.norm(relative,axis=1)+1e-12)
            assert np.all(np.sum(applied*relative,axis=1)>=0)


def test_dry_and_matched_velocity_have_no_push():
    desired,applied=limited_push([[1,0,0],[1,0,0]],[[0,0,0],[1,0,0]],[0,1],.05,.1)
    np.testing.assert_array_equal(desired,0);np.testing.assert_array_equal(applied,0)


def test_authoritative_skipped_output_interval():
    np.testing.assert_allclose(output_velocity([[.5,0,0]],[[0,0,0]],15,3,24),[[1,0,0]])
    with pytest.raises(ValueError):output_velocity([[0,0,0]],[[0,0,0]],3,3,24)


@pytest.mark.parametrize('times',[[0,.1,.2],[0,.04,.13],[.1,.14,.21]])
def test_quadratic_derivative_nonuniform_and_native_time_extrapolation(times):
    times=np.array(times)
    positions=np.array([[[3*t*t+2*t+1,0,0]] for t in times])
    for evaluation in [times[-1],times[-1]+.01]:
        np.testing.assert_allclose(quadratic_velocity(positions,times,evaluation),[[6*evaluation+2,0,0]],atol=1e-12)


def test_discrete_correction_approaches_target_without_crossing_with_exact_state():
    for interval in [.001,.01,1/24,.1]:
        for response in [.05,.1,1.]:
            a=discrete_push([[1,0,0]],[[0,0,0]],[1],response,interval)
            assert 0<a[0,0]*interval<1
            np.testing.assert_allclose(a[0,0]*interval,1-np.exp(-interval/response))
    np.testing.assert_array_equal(discrete_push([[1,0,0]],[[1,0,0]],[1],.05,.1),0)
