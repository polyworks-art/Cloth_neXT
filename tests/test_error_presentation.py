# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from cloth_next.bake.controller import BakeController
from cloth_next.bake.error_presentation import (
    UI_ERROR_DETAILS_MAX_CHARS,
    UI_ERROR_LINE_MAX_CHARS,
    UI_ERROR_SUMMARY_MAX_CHARS,
    present_error,
)
from cloth_next.bake.status import BakeSnapshot, BakeState
from cloth_next.core.error_codes import ERROR_CODES


_FORBIDDEN_UI_TEXT = (
    "traceback (most recent call last)",
    "during handling of the above exception",
    "stdout_tail=",
    "stderr_tail=",
    "progress_tail=",
    "owned_process_id=",
    'file "',
    "runtimeerror:",
    "typeerror:",
    "keyerror:",
)


def _assert_artist_facing(summary: str, details: str) -> None:
    combined = f"{summary}\n{details}".lower()
    assert summary
    assert len(summary) <= UI_ERROR_SUMMARY_MAX_CHARS
    assert len(details) <= UI_ERROR_DETAILS_MAX_CHARS
    assert all(len(line) <= UI_ERROR_LINE_MAX_CHARS
               for line in details.splitlines())
    assert details.startswith("Stage:")
    assert "Cause:" in details
    assert "What to do:" in details
    for forbidden in _FORBIDDEN_UI_TEXT:
        assert forbidden not in combined


def test_full_traceback_is_replaced_but_diagnostic_log_remains_available():
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "C:\\Users\\Artist\\AppData\\Roaming\\Blender\\5.0\\scripts\\addons\\cloth_next\\solver_test.py", line 4938, in _worker_main\n'
        "    solver.run()\n"
        "RuntimeError: low-level solver exploded\n"
        "stdout_tail=('thousands of technical lines',)\n"
        "stderr_tail=('panic in worker',)\n"
        "progress_tail=('newton=44',)\n"
        "Diagnostic log: C:\\Cache\\ClothNeXt\\failure.log"
    )

    result = present_error(
        "The solver test failed unexpectedly.", traceback,
        error_code="CNX-E199", stage="Simulation")

    _assert_artist_facing(result.summary, result.details)
    assert "Diagnostic log: C:\\Cache\\ClothNeXt\\failure.log" in result.details
    assert "unexpected internal error" in result.summary.lower()


def test_known_solver_failure_is_rewritten_for_an_artist():
    result = present_error(
        "The solver reported a failure while simulating.",
        "Cause: server status FAILED during simulating: process crashed; "
        "stderr_tail=('thread panicked at src/newton.rs:442',)\n"
        "Diagnostic log: D:\\Bakes\\failure.log",
        error_code="CNX-E164", stage="Simulation")

    _assert_artist_facing(result.summary, result.details)
    assert "solver stopped during the simulation" in result.summary.lower()
    assert "health check" in result.details.lower()
    assert "D:\\Bakes\\failure.log" in result.details


def test_useful_frame_and_action_are_preserved():
    result = present_error(
        "Simulation could not converge at Blender frame 60.",
        "Stage: Collision solve\n"
        "Solver frame: 59\n"
        "Blender frame: 60\n"
        "Cause: The simulation could not find a stable solution.\n"
        "What to do: Lower Friction and use a smaller Time Step.",
        error_code="CNX-E161")

    _assert_artist_facing(result.summary, result.details)
    assert "Blender frame: 60" in result.details
    assert "Solver frame: 59" in result.details
    assert "Lower Friction" in result.details


def test_every_public_error_code_has_bounded_understandable_copy():
    for code in ERROR_CODES:
        result = present_error("", "", error_code=code)
        _assert_artist_facing(result.summary, result.details)
        assert code not in result.summary


def test_controller_never_publishes_a_worker_traceback():
    controller = BakeController()
    controller.transition(BakeState.PREPARING)
    details = (
        "Traceback (most recent call last):\n"
        '  File "/home/runner/cloth_next/worker.py", line 12, in run\n'
        "    raise RuntimeError('boom')\n"
        "RuntimeError: boom\n"
        "Diagnostic log: /tmp/cloth-next/failure.log"
    )

    snapshot = controller.fail(
        "Unexpected Bake worker failure", details,
        error_code="CNX-E199")

    _assert_artist_facing(snapshot.error_summary, snapshot.error_details)
    assert snapshot.status_message == snapshot.error_summary
    assert snapshot.error_code == "CNX-E199"


def test_transport_resanitizes_a_directly_constructed_snapshot():
    raw = BakeSnapshot(
        state=BakeState.ERROR,
        error_code="CNX-E164",
        error_summary="The solver crashed",
        error_details=(
            "Cause: solver process crashed; stderr_tail="
            + "x" * 20_000
            + "\nTraceback (most recent call last):\n"
            + 'File "C:\\secret\\solver.py", line 1'),
        status_message="raw technical status")

    transported = raw.to_transport_dict()

    _assert_artist_facing(
        transported["error_summary"], transported["error_details"])
    assert len(transported["error_details"]) <= 1600
    assert transported["status_message"] == transported["error_summary"]


def test_new_bake_can_still_clear_previous_error_fields():
    controller = BakeController()
    controller.transition(BakeState.PREPARING)
    controller.fail("Failure", "details", error_code="CNX-E100")

    fresh = controller.transition(BakeState.PREPARING)

    assert fresh.error_summary == ""
    assert fresh.error_details == ""
    assert fresh.error_code == ""
