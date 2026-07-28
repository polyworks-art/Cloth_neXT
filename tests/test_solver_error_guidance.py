# SPDX-License-Identifier: GPL-3.0-or-later

from cloth_next.bake.error_presentation import present_error


def test_pin_infeasible_is_explained_without_solver_jargon():
    result = present_error(
        "The solver reported a failure while simulating.",
        "server status FAILED: PinInfeasible: A pinned vertex is driven into "
        "a collider it cannot yield to; stderr_tail=('panic',)",
        error_code="CNX-E160", stage="simulation")

    assert result.summary == "A Hard Pin conflicts with a Collider."
    assert "Switch the Pin Group to Soft Pin" in result.details
    assert "PinInfeasible" not in result.details
    assert "stderr_tail" not in result.details


def test_overlapping_start_points_to_clearance():
    result = present_error(
        "The solver reported a failure while building.",
        "OverlappingStart: Two surfaces start the step already touching or overlapping",
        error_code="CNX-E153", stage="scene preparation")

    assert result.summary == "The simulation starts with overlapping surfaces."
    assert "Separate the surfaces" in result.details
    assert "Collision Gap" in result.details
    assert "OverlappingStart" not in result.details


def test_newton_stall_explains_over_constraint():
    result = present_error(
        "The solver reported a failure while simulating.",
        "NewtonStall: Newton solve made no progress (over-constrained configuration)",
        error_code="CNX-E161", stage="simulation")

    assert result.summary == "The simulation is over-constrained."
    assert "Pins and collisions" in result.details
    assert "Use Soft Pins" in result.details
    assert "NewtonStall" not in result.details
