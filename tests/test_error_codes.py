from pathlib import Path

import pytest

from cloth_next.bake.controller import BakeController
from cloth_next.bake.status import BakeSnapshot, BakeState
from cloth_next.bake.transport import MAX_MESSAGE_BYTES, encode_message
from cloth_next.core.error_codes import ERROR_CODES, classify_error
from cloth_next.core import error_codes
from cloth_next.core.errors import ErrorCategory, ErrorRecord
from tools.build_error_guidance import render_markdown


LEGACY_CODES = frozenset({
    "CNX-E100", "CNX-E101", "CNX-E102", "CNX-E103", "CNX-E104",
    "CNX-E105", "CNX-E106", "CNX-E107", "CNX-E108", "CNX-E109",
    "CNX-E110", "CNX-E111", "CNX-E112", "CNX-E113", "CNX-E114",
    "CNX-E115", "CNX-E116", "CNX-E120", "CNX-E121", "CNX-E122",
    "CNX-E123", "CNX-E124", "CNX-E125", "CNX-E126", "CNX-E127",
    "CNX-E130", "CNX-E131", "CNX-E132", "CNX-E133", "CNX-E134",
    "CNX-E135", "CNX-E140", "CNX-E141", "CNX-E142", "CNX-E143",
    "CNX-E144", "CNX-E145", "CNX-E146", "CNX-E150", "CNX-E151",
    "CNX-E152", "CNX-E153", "CNX-E154", "CNX-E160", "CNX-E161",
    "CNX-E162", "CNX-E163", "CNX-E164", "CNX-E165", "CNX-E166",
    "CNX-E167", "CNX-E168", "CNX-E169", "CNX-E170", "CNX-E171",
    "CNX-E172", "CNX-E173", "CNX-E174", "CNX-E175", "CNX-E176",
    "CNX-E180", "CNX-E181", "CNX-E182", "CNX-E183", "CNX-E184",
    "CNX-E185", "CNX-E186", "CNX-E187", "CNX-E188", "CNX-E190",
    "CNX-E191", "CNX-E192", "CNX-E193", "CNX-E198", "CNX-E199",
})


def test_registry_is_unique_stable_and_actionable():
    assert len(ERROR_CODES) >= 70
    assert len(ERROR_CODES) == len(set(ERROR_CODES))
    for code, info in ERROR_CODES.items():
        assert code == info.code
        assert code.startswith("CNX-E") and len(code) == 8
        assert code[5:].isdigit()
        assert info.stage and info.cause and info.action
    assert LEGACY_CODES <= set(ERROR_CODES)


def test_public_markdown_is_generated_exactly_from_runtime_registry():
    documentation = Path("docs/ERROR_CODES.md").read_text(encoding="utf-8")
    assert documentation == render_markdown()


def test_every_classifier_target_and_stage_fallback_exists():
    targets = {code for _pattern, code in error_codes._CAUSE_RULES}
    targets.update(code for rules in error_codes._STAGE_RULES.values()
                   for _pattern, code in rules)
    targets.update(code for _pattern, code in error_codes._RULES)
    assert targets <= set(ERROR_CODES)
    assert set(error_codes.STAGE_FALLBACKS.values()) <= set(ERROR_CODES)


@pytest.mark.parametrize(("stage", "message", "expected"), (
    ("PREPARING", "No enabled deformable object", "CNX-E101"),
    ("PREPARING", "Bake range is invalid", "CNX-E102"),
    ("PREPARING", "Topology-changing modifiers are unsupported", "CNX-E103"),
    ("PREPARING", "Invalid material quality value", "CNX-E104"),
    ("PREPARING", "Animated Pinning changed Cloth topology", "CNX-E105"),
    ("PREPARING", "All deformables need the same range", "CNX-E106"),
    ("PREPARING", "Object Coat no longer exists", "CNX-E107"),
    ("PREPARING", "Force Empty has an invalid axis", "CNX-E108"),
    ("PREPARING", "Non-finite malformed geometry", "CNX-E109"),
    ("STARTING_COMPANION", "Companion manifest is missing", "CNX-E111"),
    ("STARTING_COMPANION", "Companion transport failed", "CNX-E112"),
    ("STARTING_COMPANION", "Companion process launch failed", "CNX-E113"),
    ("WAITING_FOR_COMPANION", "Companion handshake timeout", "CNX-E114"),
    ("WAITING_FOR_COMPANION", "Window did not become visible or topmost", "CNX-E115"),
    ("WAITING_FOR_COMPANION", "Invalid session token", "CNX-E116"),
    ("PREPARING", "A cache directory is required", "CNX-E121"),
    ("PREPARING", "Evaluated geometry export failed", "CNX-E122"),
    ("PREPARING", "Scene encoding failed", "CNX-E123"),
    ("PREPARING", "Capturing animated Pin targets failed", "CNX-E124"),
    ("PREPARING", "No space left on device", "CNX-E125"),
    ("PREPARING", "Bake worker could not be started", "CNX-E126"),
    ("PREPARING", "Stale partial Bake state", "CNX-E127"),
    ("PREPARING", "Animated Collider changes topology at frame 8", "CNX-E128"),
    ("PREPARING", "Recovery checkpoint cannot be resumed", "CNX-E129"),
    ("STARTING_SOLVER", "Native solver worker is missing", "CNX-E131"),
    ("STARTING_SOLVER", "Solver protocol version mismatch", "CNX-E132"),
    ("STARTING_SOLVER", "Solver did not become ready", "CNX-E133"),
    ("STARTING_SOLVER", "Solver exited during startup", "CNX-E134"),
    ("STARTING_SOLVER", "Access is denied while starting", "CNX-E135"),
    ("STARTING_SOLVER", "Port 49152 is already in use", "CNX-E136"),
    ("UPLOADING", "Upload response timed out", "CNX-E141"),
    ("UPLOADING", "Connection closed during upload", "CNX-E142"),
    ("UPLOADING", "Solver did not acknowledge the upload", "CNX-E143"),
    ("UPLOADING", "Data hash mismatch after upload", "CNX-E144"),
    ("UPLOADING", "Response was too large", "CNX-E145"),
    ("SIMULATING", "Control server exited; owned_process_ids=(12, 13)", "CNX-E146"),
    ("BUILDING", "Solver reported a failure while building", "CNX-E151"),
    ("BUILDING", "Build timed out", "CNX-E152"),
    ("BUILDING", "Geometry build failed on a degenerate face", "CNX-E153"),
    ("BUILDING", "Project is unexpectedly busy", "CNX-E154"),
    ("SIMULATING", "Linear solver failed to converge", "CNX-E161"),
    ("SIMULATING", "Intersection detected at frame 4", "CNX-E162"),
    ("SIMULATING", "Simulation stalled", "CNX-E163"),
    ("SIMULATING", "Solver process exited during simulation", "CNX-E164"),
    ("SIMULATING", "Non-finite simulation result position", "CNX-E165"),
    ("SIMULATING", "CUDA out of memory", "CNX-E166"),
    ("SIMULATING", "Finished without producing every frame", "CNX-E167"),
    ("SIMULATING", "Numerical overflow detected", "CNX-E168"),
    ("SIMULATING", "Unexpected solver state", "CNX-E169"),
    ("FETCHING", "Result transfer timed out", "CNX-E171"),
    ("FETCHING", "Connection broke during result transfer", "CNX-E172"),
    ("FETCHING", "Output map is missing", "CNX-E173"),
    ("FETCHING", "Frame 12 is missing", "CNX-E174"),
    ("FETCHING", "Frame payload is corrupt", "CNX-E175"),
    ("FETCHING", "Result vertex count mismatch", "CNX-E176"),
    ("IMPORTING", "Cache permission denied", "CNX-E181"),
    ("IMPORTING", "PC2 finalization failed", "CNX-E182"),
    ("IMPORTING", "Object topology changed before import", "CNX-E183"),
    ("IMPORTING", "Target object no longer exists", "CNX-E184"),
    ("IMPORTING", "Playback attachment failed", "CNX-E185"),
    ("IMPORTING", "Cache integrity validation failed", "CNX-E186"),
    ("IMPORTING", "Multi-object playback cache is incomplete", "CNX-E187"),
    ("IMPORTING", "Curve topology changed before Rod import", "CNX-E188"),
    ("CANCELLING", "Cancellation timed out", "CNX-E191"),
    ("CANCELLING", "Reader thread did not stop", "CNX-E192"),
    ("CANCELLING", "Temporary files could not be removed", "CNX-E193"),
    ("SIMULATING", "Worker stopped without a terminal result", "CNX-E198"),
))
def test_every_specific_public_classifier_has_a_positive_case(
        stage, message, expected):
    assert classify_error(stage, details=message) == expected


def test_specific_causes_win_over_stage_fallbacks():
    assert classify_error("SIMULATING", details=
                          "Linear solver failed to converge at frame 8") == "CNX-E161"
    assert classify_error("BUILDING", details=
                          "Initial intersection while building contacts") == "CNX-E162"
    assert classify_error("FETCHING", details=
                          "finished without producing every frame") == "CNX-E167"


def test_midrun_intersection_is_not_mistaken_for_a_crash():
    # The solver's real mid-run failure wraps the StepResult booleans; it must
    # classify as an intersection (E162), not the generic crash bucket (E164),
    # so the recovery advice is "separate/relieve geometry", not "check drivers".
    intersection = ("server error during status: Intersection detected: "
                    "advance failed at frame 3 "
                    "(ccd=true, pcg=true, intersection_free=false)")
    assert classify_error("SIMULATING", details=intersection) == "CNX-E162"
    assert classify_error("SIMULATING", details=
                          "Continuous Collision Detection failed: advance failed "
                          "at frame 5 (ccd=false, pcg=true, intersection_free=true)"
                          ) == "CNX-E162"
    # A genuine process exit still lands on E164.
    assert classify_error("SIMULATING", details=
                          "solver process exited during simulation") == "CNX-E164"


def test_parameter_instability_is_reachable_as_e168():
    assert classify_error("SIMULATING", details=
                          "Numerical overflow at frame 12") == "CNX-E168"
    assert classify_error("SIMULATING", details=
                          "BVH traversal stack overflow") == "CNX-E168"


def test_e161_recommends_the_most_reliable_recovery_first():
    action = ERROR_CODES["CNX-E161"].action
    assert action.startswith("Lower Friction first.")
    assert action.index("Friction") < action.index("Time Step")
    assert classify_error("IMPORTING", details=
                          "Curve topology changed before Rod import") == "CNX-E188"
    assert classify_error("SIMULATING", details=
                          "RAM safety threshold reached") == "CNX-E166"
    assert classify_error("IMPORTING", details=
                          "Multi-object playback cache is invalid") == "CNX-E187"
    assert classify_error("IMPORTING", details=
                          "Multi-object playback cache topology mismatch") == "CNX-E187"
    assert classify_error("IMPORTING", details=
                          "Cache is damaged: metadata missing/invalid fields") == "CNX-E186"


def test_typed_category_and_stage_fallbacks_remain_compatible():
    record = ErrorRecord.create(
        category=ErrorCategory.PROTOCOL_COMPATIBILITY,
        user_message="unsupported service", technical_message="future wire",
        recommended_action="upgrade")
    assert classify_error("STARTING_SOLVER", record=record) == "CNX-E132"
    assert classify_error("SIMULATING", "unknown solve failure") == "CNX-E160"


def _record(*, failure_kind="", crash_kind="", active_operation="",
            user_message="The operation failed", technical_message=""):
    context = {}
    if failure_kind:
        context["failure_kind"] = failure_kind
    if crash_kind:
        context["crash_kind"] = crash_kind
    if active_operation:
        context["active_operation"] = active_operation
    return ErrorRecord.create(
        category=ErrorCategory.SOLVER_CONNECTION,
        user_message=user_message,
        technical_message=technical_message or failure_kind,
        recommended_action="Retry after reviewing diagnostics",
        context=context)


@pytest.mark.parametrize(("record", "expected"), (
    (_record(failure_kind="PORT_ALREADY_OCCUPIED"), "CNX-E136"),
    (_record(failure_kind="STARTUP_TIMEOUT_ALIVE"), "CNX-E133"),
    (_record(failure_kind="CRASH_BEFORE_READY"), "CNX-E134"),
    (_record(failure_kind="CRASH_DURING_FRAME_FETCH",
             active_operation="FETCHING"), "CNX-E172"),
    (_record(failure_kind="TRANSPORT_LOST_PROCESS_ALIVE",
             active_operation="UPLOADING"), "CNX-E142"),
    (_record(crash_kind="device_assert",
             active_operation="SIMULATING"), "CNX-E164"),
))
def test_structured_solver_diagnostics_classify_without_fragile_text(
        record, expected):
    assert classify_error("SIMULATING", record=record) == expected


def test_proven_simulation_cause_beats_structured_generic_crash():
    record = _record(
        failure_kind="CRASH_DURING_SIMULATION",
        active_operation="SIMULATING",
        technical_message="Intersection detected at frame 19")
    assert classify_error("SIMULATING", record=record) == "CNX-E162"


@pytest.mark.parametrize(("stage", "message", "expected", "not_expected"), (
    ("STARTING_SOLVER", "Solver did not become ready", "CNX-E133", "CNX-E114"),
    ("PREPARING", "Animated Collider Coat changes topology", "CNX-E128", "CNX-E103"),
    ("PREPARING", "The recovery project has no confirmed Saved State",
     "CNX-E129", "CNX-E116"),
    ("PREPARING", "The saved solver project does not match this Bake",
     "CNX-E129", "CNX-E144"),
    ("IMPORTING", "Target object no longer exists", "CNX-E184", "CNX-E107"),
    ("FETCHING", "Connection closed during result transfer", "CNX-E172", "CNX-E142"),
    ("CANCELLING", "Temporary files could not be removed: access denied",
     "CNX-E193", "CNX-E135"),
))
def test_stage_specific_errors_do_not_false_match_other_subsystems(
        stage, message, expected, not_expected):
    result = classify_error(stage, details=message)
    assert result == expected
    assert result != not_expected


def test_control_server_exit_has_distinct_connection_code():
    assert classify_error(
        "SIMULATING",
        details=("PPF control server exited unexpectedly; "
                 "owned_process_ids=(123, 456)")) == "CNX-E146"


def test_recovery_traceback_function_name_is_not_misclassified_as_pin_error():
    details = (
        "SceneValidationError: Resume was refused because the newest solver "
        "checkpoint predates the playable cache prefix.\n"
        "  File \"solver_test.py\", line 6593, in _pin_capture_pump")
    assert classify_error("PREPARING", details=details) == "CNX-E127"
    assert classify_error(
        "PREPARING",
        details="Animated Pinning changed Cloth topology") == "CNX-E105"


def test_gravity_diagnostics_without_a_force_are_not_misclassified_as_e108():
    assert classify_error(
        "PREPARING", details="Newton scene gravity contains a non-finite value"
    ) == "CNX-E109"
    assert classify_error(
        "PREPARING", details="Gravity Force Empty has an invalid local Z axis"
    ) == "CNX-E108"


def test_controller_accepts_only_registered_explicit_codes():
    controller = BakeController()
    controller.transition(BakeState.PREPARING)
    assert controller.fail("failed", error_code="CNX-E166").error_code == "CNX-E166"
    controller.reset()
    controller.transition(BakeState.PREPARING)
    assert controller.fail("failed", error_code="NOT-A-CODE").error_code == "CNX-E100"


def test_transport_bounds_large_diagnostics_and_recovers_unknown_enums():
    snapshot = BakeSnapshot(state=BakeState.ERROR, error_code="CNX-E199",
                            error_details="x" * (MAX_MESSAGE_BYTES * 2))
    encoded = encode_message("bake_status", "token", snapshot)
    assert len(encoded) <= MAX_MESSAGE_BYTES
    recovered = BakeSnapshot.from_dict({
        "state": "FUTURE_STATE", "job_kind": "FUTURE_KIND",
        "activity_code": "FUTURE_ACTIVITY", "elapsed_seconds": "nan",
        "progress_current": "broken", "progress_total": "broken"})
    assert recovered.state is BakeState.ERROR
    assert recovered.error_code == "CNX-E116"
    assert recovered.progress_current == 0
    assert recovered.progress_total is None
