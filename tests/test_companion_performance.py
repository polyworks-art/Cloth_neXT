from types import SimpleNamespace

from cloth_next.bake.status import BakeState
from companion.performance_graph import FramePerformanceHistory
from companion.app import format_frame_time


def snapshot(frame, elapsed, *, job="job", state=BakeState.SIMULATING):
    return SimpleNamespace(job_id=job, state=state, current_frame=frame,
                           elapsed_seconds=elapsed)


def test_frame_performance_records_real_completed_frame_durations():
    history=FramePerformanceHistory()
    assert not history.observe(snapshot(1,1.0))
    assert history.observe(snapshot(2,2.0))
    assert history.latest_seconds == 1.0
    history.observe(snapshot(3,2.5))
    assert history.latest_seconds == .5
    history.observe(snapshot(4,4.5))
    assert history.latest_seconds == 2.0
    assert history.average_seconds == .7*.85+.3*2.0


def test_skipped_status_updates_fill_each_observed_frame():
    history=FramePerformanceHistory()
    history.observe(snapshot(10,1.0))
    history.observe(snapshot(14,3.0))
    assert len(history.durations) == 4
    assert len(set(history.durations)) == 1


def test_new_job_resets_graph_and_non_simulation_does_not_add_samples():
    history=FramePerformanceHistory()
    history.observe(snapshot(1,1.0)); history.observe(snapshot(2,2.0))
    assert history.durations
    history.observe(snapshot(1,0.0,job="next",state=BakeState.PREPARING))
    assert not history.durations
    assert history.display_range is None


def test_frame_time_range_expands_immediately_and_contracts_smoothly():
    history=FramePerformanceHistory()
    history.observe(snapshot(1,0.0)); history.observe(snapshot(2,1.0))
    first=history.display_range
    history.observe(snapshot(3,11.0))
    expanded=history.display_range
    assert first is not None and expanded is not None
    assert expanded[1] >= 10.0
    history.observe(snapshot(4,12.0))
    assert history.display_range[1] > 1.0


def test_frame_time_label_has_explicit_units():
    assert format_frame_time(58.25) == "58.2 s"
    assert format_frame_time(None) == "—"

