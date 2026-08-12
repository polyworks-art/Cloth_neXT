def test_smooth_motion_is_responsive_and_bounded(blender_env):
    from cloth_next.blender import viewport_autoframe
    alpha = viewport_autoframe.interpolation_alpha("SMOOTH", 0.18)
    assert 0.18 < alpha < 1.0


def test_cinematic_motion_is_slower_than_smooth(blender_env):
    from cloth_next.blender import viewport_autoframe
    cinematic = viewport_autoframe.interpolation_alpha("CINEMATIC", 0.18)
    smooth = viewport_autoframe.interpolation_alpha("SMOOTH", 0.18)
    assert 0.0 < cinematic < smooth


def test_interpolation_clamps_invalid_response_values(blender_env):
    from cloth_next.blender import viewport_autoframe
    assert 0.0 < viewport_autoframe.interpolation_alpha("SMOOTH", -10.0) < 1.0
    assert viewport_autoframe.interpolation_alpha("SMOOTH", 10.0) == 1.0
