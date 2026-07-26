from fractions import Fraction

from cloth_next.sample_plan import build_sample_plan


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
