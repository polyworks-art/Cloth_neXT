from __future__ import annotations

from copy import deepcopy
import math

import pytest

from companion.particle_motion import advance_particle, smooth_rate


def particle():
    return {"base_x":32.0, "base_y":30.0,
            "direction_x":0.8, "direction_y":0.6, "speed":18.0,
            "noise_time":0.0, "phase":0.4, "phase_2":1.7,
            "frequency":0.8, "frequency_2":1.6, "amplitude":3.0}


def test_particle_path_is_frame_rate_independent():
    fast, slow = deepcopy(particle()), deepcopy(particle())
    fast_position = slow_position = None
    for _ in range(60):
        fast_position = advance_particle(fast, 1 / 60, 0.7, 200, 200)
    for _ in range(20):
        slow_position = advance_particle(slow, 1 / 20, 0.7, 200, 200)
    assert fast_position == pytest.approx(slow_position)
    assert fast["base_x"] == pytest.approx(slow["base_x"])
    assert fast["base_y"] == pytest.approx(slow["base_y"])


def test_path_noise_stays_bounded_instead_of_accumulating():
    moving = particle()
    moving.update(direction_x=1.0, direction_y=0.0, base_x=20.0,
                  base_y=30.0, speed=4.0)
    positions = [advance_particle(moving, 0.05, 1.0, 1000, 1000)
                 for _ in range(200)]
    assert max(abs(y - moving["base_y"]) for _x, y in positions) <= 4.05


def test_rate_smoothing_depends_on_elapsed_time_not_frame_count():
    fast = 0.18
    for _ in range(10):
        fast = smooth_rate(fast, 1.0, 0.1)
    slow = smooth_rate(0.18, 1.0, 1.0)
    # Individual elapsed values are intentionally clamped to 100 ms, so ten
    # real timer steps equal one second of response without catch-up jumps.
    expected = 1.0 + (0.18 - 1.0) * math.exp(-5.0)
    assert fast == pytest.approx(expected)
    assert slow > 0.18
