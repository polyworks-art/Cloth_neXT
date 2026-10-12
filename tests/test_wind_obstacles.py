# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
import numpy as np
from cloth_next.gaia.wind_field import WindSource, velocity
from cloth_next.gaia.wind_obstacles import ObstacleSamples, obstacle_velocity


class PlaneSampler:
    """Analytic X=0 surface; tests the model independently from Blender BVH."""
    def sample(self, points, direction, reach):
        self.direction = direction.copy()
        x = points[:, 0]
        normal = np.zeros_like(points); normal[:, 0] = np.where(x < 0, -1., 1.)
        blocked = (x * direction[0] > 0) & (abs(x) < reach)
        centers = np.zeros_like(points)
        return ObstacleSamples(abs(x), normal, centers, np.ones(len(x)), x == 0,
            np.where(blocked, abs(x), np.inf), np.ones(len(x)), centers)


def source(**kwargs):
    base = WindSource((0,0,0),(1,0,0),2.,4.,2.,0.,1.,3.,17, 'SCENE')
    return replace(base, **kwargs)


def test_shadow_recovery_surface_deflection_and_solid_interior():
    points = np.array([[-.1,.2,0], [.2,0,0], [2.,0,0], [0,0,0]])
    flow = np.tile([2.,0,0], (4,1))
    result = obstacle_velocity(points, flow, source(), .3, PlaneSampler(), wake_strength=0)
    assert result[0,1] > 0, 'Near the front surface, flow routes towards its side'
    assert 0 < result[1,0] < result[2,0] < 2, 'Wind recovers downstream'
    np.testing.assert_array_equal(result[3], [0,0,0])
    assert np.isfinite(result).all()


def test_wake_is_deterministic_temporal_and_bounded():
    points = np.array([[.2,.2,0],[.5,.3,0]])
    flow = np.tile([2.,0,0], (2,1)); sampler = PlaneSampler()
    a = obstacle_velocity(points, flow, source(), .3, sampler)
    np.testing.assert_array_equal(a, obstacle_velocity(points, flow, source(), .3, sampler))
    b = obstacle_velocity(points, flow, source(), .7, sampler)
    assert not np.allclose(a[:,1:], b[:,1:])
    assert np.linalg.norm(a,axis=1).max() <= 3


def test_each_source_traces_in_its_own_direction():
    p = np.array([[.5,0,0]])
    forward = PlaneSampler(); reverse = PlaneSampler()
    a = velocity(p, [source()], 0, forward)
    b = velocity(p, [source(direction=(-1,0,0))], 0, reverse)
    np.testing.assert_array_equal(forward.direction, [1,0,0])
    np.testing.assert_array_equal(reverse.direction, [-1,0,0])
    assert np.linalg.norm(a) < np.linalg.norm(b)


def test_off_mode_and_zero_contribution_are_exact_noops():
    p = np.array([[0,0,0],[5,0,0]])
    class RefuseQueries:
        def sample(self,*_args):
            raise AssertionError('Disabled or zero Wind queried geometry')
    a = source(obstacle_mode='NONE')
    np.testing.assert_array_equal(velocity(p,[a],0), velocity(p,[a],0,RefuseQueries()))
    np.testing.assert_array_equal(velocity(p,[source(speed=0)],0,RefuseQueries()), np.zeros_like(p))
