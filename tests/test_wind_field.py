# SPDX-License-Identifier: GPL-3.0-or-later
from dataclasses import replace
import zlib
import numpy as np
import pytest
from cloth_next.gaia.wind_field import WindSource, velocity, sample_points, encode_grid, arrow_lines
from cloth_next.gaia.water_field import WaterVelocityField, solver_grid


def source(**kwargs):
    defaults = dict(center=(0, 0, 0), direction=(1, 0, 0), speed=2., radius=2.,
                    falloff=2., turbulence=0., noise_size=1., noise_time=3., seed=17)
    return WindSource(**(defaults | kwargs))


def test_local_wind_falloff_and_outside_support():
    values = velocity([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]], [source()], 0)
    np.testing.assert_allclose(values, [[2, 0, 0], [1.125, 0, 0], [0, 0, 0], [0, 0, 0]])


def test_translation_direction_and_superposition():
    points = np.array([[0, 0, 0], [.3, .4, .5]])
    a = source(); moved = replace(a, center=(10, 2, -3))
    np.testing.assert_allclose(velocity(points, [a], .5), velocity(points + moved.center, [moved], .5))
    rotated = source(direction=(0, 0, -4))
    np.testing.assert_allclose(velocity([[0, 0, 0]], [rotated], 0), [[0, 0, -2]])
    np.testing.assert_allclose(velocity(points, [a, a], 0), 2 * velocity(points, [a], 0))


def test_turbulence_is_spatial_temporal_reproducible_and_bounded():
    points = sample_points((-1, -1, -1), (1, 1, 1), (5, 5, 5))
    field = source(speed=0, turbulence=3)
    a = velocity(points, [field], .2)
    np.testing.assert_array_equal(a, velocity(points, [field], .2))
    assert not np.allclose(a, velocity(points, [field], .6))
    assert np.ptp(a[..., 0]) > .1
    assert np.linalg.norm(a, axis=-1).max() <= 3
    assert not np.allclose(a, velocity(points, [replace(field, seed=18)], .2))


def test_wind_grid_matches_existing_official_axis_conversion():
    rng = np.random.default_rng(42)
    values = rng.normal(size=(2, 3, 4, 5, 3)).astype(np.float32)
    lo, hi = (-2, -3, -4), (5, 6, 7)
    encoded = encode_grid(values, lo, hi, [0., .5])
    expected = []
    for frame in values:
        a, b, converted = solver_grid(WaterVelocityField(lo, hi, frame, np.ones(frame.shape[:3], np.float32)))
        expected.append(converted)
    actual = np.frombuffer(zlib.decompress(encoded['data']), dtype='<f4').reshape(encoded['shape'])
    np.testing.assert_array_equal(actual, np.stack(expected))
    assert encoded['min'] == list(a) and encoded['max'] == list(b)
    assert encoded['kind'] == 'air-velocity'


def test_preview_arrows_are_float32_bounded_and_point_along_wind():
    arrows = arrow_lines([[0, 0, 0], [1, 0, 0]], [[1000, 0, 0], [0, 0, 0]], .2, .5)
    assert arrows.dtype == np.float32 and arrows.flags.c_contiguous
    assert arrows.shape == (6, 3)
    np.testing.assert_allclose(arrows[:2], [[0, 0, 0], [.5, 0, 0]])
    assert np.linalg.norm(arrows, axis=1).max() <= .5


@pytest.mark.parametrize('kwargs', [{'radius':0}, {'noise_size':0}, {'falloff':.5}, {'speed':float('nan')}])
def test_invalid_sources_fail_before_sampling(kwargs):
    with pytest.raises(ValueError):
        source(**kwargs)
