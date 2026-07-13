import pytest

from cloth_next.bake.frame_range import (BakeFrameRange, BakeRangeError,
                                          MAX_OUTPUT_FRAMES)


@pytest.mark.parametrize("start,end,count,steps", [
    (1, 8, 8, 7),
    (1, 250, 250, 249),
    (20, 30, 11, 10),
    (100, 105, 6, 5),
])
def test_blender_to_ppf_mapping(start, end, count, steps):
    selected = BakeFrameRange(start, end)
    assert selected.output_count == count
    assert selected.solver_steps == steps
    assert selected.blender_frame(0) == start
    assert selected.blender_frame(steps) == end
    assert selected.progress(0) == (1, count)
    assert selected.progress(steps) == (count, count)


def test_zero_step_and_reversed_ranges_are_rejected():
    with pytest.raises(BakeRangeError, match="zero-step"):
        BakeFrameRange(20, 20)
    with pytest.raises(BakeRangeError, match="greater than"):
        BakeFrameRange(20, 19)


def test_safety_limit_is_explicit_and_never_clamps():
    with pytest.raises(BakeRangeError, match=str(MAX_OUTPUT_FRAMES)):
        BakeFrameRange(1, MAX_OUTPUT_FRAMES + 1)


@pytest.mark.parametrize("start,end", [(1.0, 8), (1, float("nan")),
                                        (True, 8), (1, "8")])
def test_invalid_values_never_form_a_range(start, end):
    with pytest.raises(BakeRangeError):
        BakeFrameRange(start, end)
