from fractions import Fraction

import pytest

from cloth_next.sample_plan import build_collider_timeline, build_sample_plan


def test_union_sample_plan_contains_each_time_once():
    plan = build_sample_plan(1, 3, collider_samples=(2, 4))
    positions = [point.position for point in plan]
    assert positions == sorted(set(positions))
    assert positions[0] == Fraction(1)
    assert positions[-1] == Fraction(3)
    assert len(plan) == 9


def test_multiple_colliders_share_one_timeline():
    one = build_sample_plan(1, 2, collider_samples=(8,))
    many = build_sample_plan(1, 2, collider_samples=(8, 8, 8, 8))
    assert many == one


def test_canonical_collider_timeline_maps_frames_1_to_64_exactly():
    timeline = build_collider_timeline(
        1, 64, samples_per_frame=8, fps=30.0)

    assert len(timeline.points) == 505
    assert timeline.logical_frame_count == 64
    assert timeline.points[0].position == Fraction(1)
    assert timeline.points[1].position == Fraction(9, 8)
    assert timeline.points[7].position == Fraction(15, 8)
    assert timeline.points[8].position == Fraction(2)
    assert timeline.points[-1].position == Fraction(64)
    assert timeline.duration_seconds == pytest.approx(2.1)
    assert timeline.times[0] == 0.0
    assert timeline.times[-1] == pytest.approx(2.1)


@pytest.mark.parametrize("samples_per_frame", [1, 2, 4, 8])
def test_sample_density_does_not_change_logical_duration(samples_per_frame):
    timeline = build_collider_timeline(
        1, 64, samples_per_frame=samples_per_frame, fps=30.0)

    assert timeline.logical_frame_count == 64
    assert timeline.duration_seconds == pytest.approx(2.1)
    assert len(timeline.points) == 63 * samples_per_frame + 1
