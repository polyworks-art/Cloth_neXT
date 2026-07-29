from types import SimpleNamespace

from cloth_next.bake.status import BakeState
from companion.performance_graph import FramePerformanceHistory


def snapshot(frame, elapsed, *, job="job", state=BakeState.SIMULATING,
             updated_at=None):
    return SimpleNamespace(job_id=job, state=state, current_frame=frame,
                           elapsed_seconds=elapsed, updated_at=updated_at)


def test_frame_performance_scores_relative_speed_per_frame():
    history=FramePerformanceHistory()
    assert not history.observe(snapshot(1,1.0))
    assert history.observe(snapshot(2,2.0))
    assert history.latest == 50
    history.observe(snapshot(3,2.5))
    assert history.latest == 100
    history.observe(snapshot(4,4.5))
    assert history.latest < 50


def test_skipped_status_updates_fill_each_observed_frame():
    history=FramePerformanceHistory()
    history.observe(snapshot(10,1.0))
    history.observe(snapshot(14,3.0))
    assert len(history.scores) == 4
    assert len(set(history.scores)) == 1


def test_new_job_resets_graph_and_non_simulation_does_not_add_samples():
    history=FramePerformanceHistory()
    history.observe(snapshot(1,1.0)); history.observe(snapshot(2,2.0))
    assert history.scores
    history.observe(snapshot(1,0.0,job="next",state=BakeState.PREPARING))
    assert not history.scores


def test_frame_time_uses_exact_transition_timestamp_not_stale_ui_elapsed():
    history=FramePerformanceHistory()
    history.observe(snapshot(1,10.0,updated_at=100.0))
    assert history.observe(snapshot(2,10.0,updated_at=102.5))
    assert history.average_frame_seconds == 2.5


def test_repeated_substep_updates_for_same_logical_frame_are_not_samples():
    history=FramePerformanceHistory()
    history.observe(snapshot(4,1.0,updated_at=100.0))
    for timestamp in (100.1,100.2,100.3,100.4):
        assert not history.observe(snapshot(4,1.0,updated_at=timestamp))
    assert not history.scores
    assert history.observe(snapshot(5,2.0,updated_at=102.0))
    assert len(history.scores) == 1
    assert history.average_frame_seconds == 2.0

