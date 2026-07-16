from pathlib import Path

from cloth_next.bake.controller import BakeController
from cloth_next.bake.status import BakeSnapshot, BakeState
from cloth_next.bake.transport import MAX_MESSAGE_BYTES, encode_message
from cloth_next.core.error_codes import ERROR_CODES, classify_error
from cloth_next.core.errors import ErrorCategory, ErrorRecord


def test_registry_is_unique_stable_and_actionable():
    assert len(ERROR_CODES) >= 70
    assert len(ERROR_CODES) == len(set(ERROR_CODES))
    for code, info in ERROR_CODES.items():
        assert code == info.code
        assert code.startswith("CNX-E") and len(code) == 8
        assert code[5:].isdigit()
        assert info.stage and info.cause and info.action


def test_public_markdown_documents_every_registered_code():
    documentation = Path("docs/ERROR_CODES.md").read_text(encoding="utf-8")
    missing = [code for code in ERROR_CODES if f"`{code}`" not in documentation]
    assert missing == []


def test_specific_causes_win_over_stage_fallbacks():
    assert classify_error("SIMULATING", details=
                          "Linear solver failed to converge at frame 8") == "CNX-E161"
    assert classify_error("BUILDING", details=
                          "Initial intersection while building contacts") == "CNX-E162"
    assert classify_error("FETCHING", details=
                          "finished without producing every frame") == "CNX-E167"
    assert classify_error("IMPORTING", details=
                          "Curve topology changed before Rod import") == "CNX-E188"
    assert classify_error("SIMULATING", details=
                          "RAM safety threshold reached") == "CNX-E166"


def test_typed_category_and_stage_fallbacks_remain_compatible():
    record = ErrorRecord.create(
        category=ErrorCategory.PROTOCOL_COMPATIBILITY,
        user_message="unsupported service", technical_message="future wire",
        recommended_action="upgrade")
    assert classify_error("STARTING_SOLVER", record=record) == "CNX-E132"
    assert classify_error("SIMULATING", "unknown solve failure") == "CNX-E160"


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
