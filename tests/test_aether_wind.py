# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER wind input: the same air-velocity field as the Wind preview and the GAIA export."""
import numpy as np

from cloth_next.aether_engine import wind as awind
from cloth_next.gaia.wind_field import WindSource, velocity


def _source(**kw):
    base = dict(center=(0.2, -0.1, 1.0), direction=(0.0, 1.0, 0.2), speed=4.0, radius=2.0, falloff=2.0,
                turbulence=1.5, noise_size=0.7, noise_time=1.3, seed=12345)
    base.update(kw)
    return WindSource(**base)


def test_engine_field_equals_the_preview_and_gaia_field():
    src = _source()
    rng = np.random.default_rng(3)
    points = rng.uniform(-1.0, 2.0, size=(200, 3))
    start = 1.0 / 24.0 * 7          # the engine's frame 0 is scene frame 7
    inp = awind.wind_input([src], air_density=0.01, air_friction=0.2, time_step=0.001,
                           seconds_at_engine_frame0=start)
    for t in (0.0, 0.5, 1.7):
        assert np.allclose(awind.velocity(points, inp, t), velocity(points, [src], start + t), atol=1e-6)
    assert inp["drag"] == 10.0 and inp["friction"] == 0.2


def test_no_air_density_switches_the_wind_off():
    inp = awind.wind_input([_source()], air_density=0.0, air_friction=0.2, time_step=0.001,
                           seconds_at_engine_frame0=0.0)
    assert inp["drag"] == 0.0 and inp["sources"] == []
