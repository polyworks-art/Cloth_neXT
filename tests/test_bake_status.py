from dataclasses import FrozenInstanceError
import threading

import pytest

from cloth_next.bake.controller import BakeController, InvalidTransition
from cloth_next.bake.status import (ACTIVITY_LABELS, BakeActivity, BakeSnapshot,
                                    BakeState, FrameEtaEstimator,
                                    format_duration)
from cloth_next.bake.transport import (MAX_MESSAGE_BYTES, decode_message,
                                       encode_message, validate_localhost)


def test_full_transition_and_cancel_paths():
    c = BakeController()
    for state in (BakeState.PREPARING, BakeState.EXPORTING,
                  BakeState.STARTING_SOLVER, BakeState.SIMULATING,
                  BakeState.IMPORTING, BakeState.FINISHED):
        c.transition(state)
    c.transition(BakeState.PREPARING)
    assert c.request_cancel().state is BakeState.CANCELLING
    assert c.transition(BakeState.CANCELLED).state is BakeState.CANCELLED


def test_invalid_transition_rejected_without_mutation():
    c = BakeController()
    before = c.snapshot()
    with pytest.raises(InvalidTransition):
        c.transition(BakeState.SIMULATING)
    assert c.snapshot() is before


def test_progress_unknown_zero_and_clamped():
    assert BakeSnapshot(progress_current=8).progress_fraction == 0
    assert BakeSnapshot(progress_current=8, progress_total=0).progress_fraction == 0
    assert BakeSnapshot(progress_current=15, progress_total=10).progress_fraction == 1
    assert BakeSnapshot(progress_current=-2, progress_total=10).progress_fraction == 0


def test_snapshot_immutable_round_trip_and_duration():
    snap = BakeSnapshot(state=BakeState.SIMULATING, progress_current=2,
                        progress_total=4)
    with pytest.raises(FrozenInstanceError):
        snap.job_id = "changed"
    assert BakeSnapshot.from_json(snap.to_json()) == snap
    assert format_duration(None) == "Unknown"
    assert format_duration(65) == "01:05"
    assert format_duration(3661, approximate=True) == "~01:01:01"


def test_frame_eta_waits_for_samples_and_smooths_real_progress():
    estimator = FrameEtaEstimator(smoothing=0.5, minimum_samples=2)
    assert estimator.observe(1, 10, 0.0) is None
    assert estimator.observe(2, 10, 2.0) is None
    assert estimator.observe(4, 10, 4.0) == 9.0
    assert estimator.observe(4, 10, 5.0) == 9.0
    assert estimator.observe(10, 10, 6.0) == 0.0


def test_frame_eta_hides_when_result_conversion_restarts_progress():
    estimator = FrameEtaEstimator(minimum_samples=1)
    assert estimator.observe(10, 20, 1.0) is None
    assert estimator.observe(11, 20, 2.0) == 9.0
    assert estimator.observe(1, 20, 3.0) is None

def test_typed_activity_round_trip_and_backward_compatibility():
    snap=BakeSnapshot(activity_code=BakeActivity.SOLVING_CONSTRAINTS,
                      activity_label="Solving constraints")
    assert BakeSnapshot.from_json(snap.to_json()) == snap
    assert BakeSnapshot.from_dict({"state":"SIMULATING"}).activity_code is BakeActivity.IDLE
    assert BakeSnapshot.from_dict({"activity_code":"future"}).activity_code is BakeActivity.UNKNOWN
    assert ACTIVITY_LABELS[BakeActivity.BUILDING_CONTACTS] == "Building contact constraints"
    assert ACTIVITY_LABELS[BakeActivity.BUILDING_PC2] == "Building PC2 cache"
    assert ACTIVITY_LABELS[BakeActivity.CAPTURING_COLLIDER_MOTION] == \
        "Capturing animated Colliders"


def test_thread_safe_reads_are_complete_snapshots():
    c = BakeController()
    c.transition(BakeState.PREPARING, progress_total=100)
    errors = []
    def writer():
        for value in range(101):
            c.update(progress_current=value)
    thread = threading.Thread(target=writer)
    thread.start()
    while thread.is_alive():
        snap = c.snapshot()
        if not 0 <= snap.progress_fraction <= 1:
            errors.append(snap)
    thread.join()
    assert not errors


def test_error_and_authenticated_bounded_schema():
    c = BakeController()
    c.transition(BakeState.PREPARING)
    snap = c.fail("Preview failure", "details")
    assert snap.error_code == "CNX-E100"
    assert snap.activity_detail == "scene validation"
    assert snap.error_details.startswith("Stage: scene validation")
    assert "What to do:" in snap.error_details
    token = "secret"
    assert decode_message(encode_message("status", token, snap), token)["snapshot"] == snap
    with pytest.raises(PermissionError):
        decode_message(encode_message("cancel", token), "wrong")
    with pytest.raises(ValueError):
        decode_message(b"not json", token)
    with pytest.raises(ValueError):
        decode_message(b"x" * (MAX_MESSAGE_BYTES + 1), token)
    validate_localhost("127.0.0.1")
    with pytest.raises(ValueError):
        validate_localhost("0.0.0.0")


def test_error_codes_follow_the_failed_bake_stage():
    c=BakeController(); c.transition(BakeState.PREPARING)
    c.transition(BakeState.EXPORTING)
    c.transition(BakeState.STARTING_SOLVER)
    c.transition(BakeState.UPLOADING)
    c.transition(BakeState.BUILDING)
    c.transition(BakeState.SIMULATING)
    assert c.fail("simulation failed").error_code=="CNX-E160"
