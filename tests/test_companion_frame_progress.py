from types import SimpleNamespace

import pytest

from cloth_next.bake.status import BakeState
from companion.frame_progress import CurrentFrameProgressEstimator


def snapshot(frame, elapsed, *, state=BakeState.SIMULATING,
             job="job", process=10):
    return SimpleNamespace(
        job_id=job, solver_process_id=process, state=state,
        current_frame=frame, elapsed_seconds=elapsed)


def complete(estimator, frame, elapsed):
    return estimator.observe(
        snapshot(frame + 1, elapsed, state=BakeState.FETCHING), now=elapsed)


def test_estimate_uses_completed_frames_and_smoothed_duration():
    estimator = CurrentFrameProgressEstimator(minimum_samples=2)
    estimator.observe(snapshot(1, 0.0), now=0.0)
    assert complete(estimator, 1, 2.0).fraction == 1.0
    estimator.observe(snapshot(2, 2.0), now=2.0)
    assert complete(estimator, 2, 6.0).fraction == 1.0

    assert estimator.sample_count == 2
    assert estimator.estimated_duration == pytest.approx(2.6)


def test_progress_is_indeterminate_until_enough_completed_frames():
    estimator = CurrentFrameProgressEstimator(minimum_samples=2)
    assert estimator.observe(snapshot(1, 0.0), now=0.0).indeterminate
    complete(estimator, 1, 1.0)
    assert estimator.observe(snapshot(2, 1.0), now=1.0).indeterminate
    complete(estimator, 2, 2.0)
    state = estimator.observe(snapshot(3, 2.0), now=2.0)
    assert not state.indeterminate
    assert state.fraction == 0.0


def test_slow_frame_clamps_at_95_percent_until_real_completion():
    estimator = CurrentFrameProgressEstimator(
        minimum_samples=1, visual_response=0.01)
    estimator.observe(snapshot(1, 0.0), now=0.0)
    complete(estimator, 1, 1.0)
    estimator.observe(snapshot(2, 1.0), now=1.0)

    state = estimator.observe(snapshot(2, 4.0), now=4.0)
    state = estimator.tick(now=5.0)
    assert state.fraction == pytest.approx(0.95)
    assert not state.complete

    state = complete(estimator, 2, 5.1)
    assert state.fraction == 1.0
    assert state.complete


def test_next_frame_resets_to_zero_after_completion():
    estimator = CurrentFrameProgressEstimator(minimum_samples=1)
    estimator.observe(snapshot(7, 10.0), now=10.0)
    assert complete(estimator, 7, 12.0).fraction == 1.0

    state = estimator.observe(snapshot(8, 12.0), now=12.0)
    assert state.fraction == 0.0
    assert not state.complete


@pytest.mark.parametrize(
    "replacement",
    [
        snapshot(1, 0.0, job="new"),
        snapshot(1, 0.0, process=11),
        snapshot(1, 0.0, state=BakeState.CANCELLING),
        snapshot(1, 0.0, state=BakeState.CANCELLED),
        snapshot(1, 0.0, state=BakeState.PREPARING),
    ])
def test_new_bake_restart_resume_or_cancel_resets_estimate(replacement):
    estimator = CurrentFrameProgressEstimator(minimum_samples=1)
    estimator.observe(snapshot(1, 0.0), now=0.0)
    complete(estimator, 1, 1.0)
    assert estimator.estimated_duration == 1.0

    estimator.observe(replacement, now=2.0)
    assert estimator.estimated_duration is None
    assert estimator.sample_count == 0
    assert estimator.tick(now=3.0).fraction == 0.0
