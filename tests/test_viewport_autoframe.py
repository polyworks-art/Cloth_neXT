import math


def test_motion_is_refresh_rate_independent(blender_env):
    from cloth_next.blender import viewport_autoframe
    one_long_step = viewport_autoframe.interpolation_alpha("SMOOTH", 0.18, 0.1)
    short_step = viewport_autoframe.interpolation_alpha("SMOOTH", 0.18, 0.05)
    two_short_steps = 1.0 - (1.0 - short_step) ** 2
    assert math.isclose(one_long_step, two_short_steps)


def test_cinematic_motion_is_slower_than_smooth(blender_env):
    from cloth_next.blender import viewport_autoframe
    cinematic = viewport_autoframe.interpolation_alpha("CINEMATIC", 0.18)
    smooth = viewport_autoframe.interpolation_alpha("SMOOTH", 0.18)
    assert 0.0 < cinematic < smooth < 1.0


def test_target_dead_zone_prevents_camera_hunting(blender_env):
    from cloth_next.blender import viewport_autoframe
    assert viewport_autoframe.stabilized_target(10.0, 10.05, 0.1) == 10.0
    assert viewport_autoframe.stabilized_target(10.0, 10.2, 0.1) == 10.2


def test_interpolation_clamps_response_values(blender_env):
    from cloth_next.blender import viewport_autoframe
    assert 0.0 < viewport_autoframe.interpolation_alpha(
        "SMOOTH", -10.0) < 1.0
    assert viewport_autoframe.interpolation_alpha("SMOOTH", 10.0) == 1.0
