# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cloth_next.core.state import ApplicationState
from cloth_next.ppf.status import WireStatus, application_state_hint, parse_status


@pytest.mark.parametrize("wire,expected", [
    (WireStatus.NO_DATA, None), (WireStatus.NO_BUILD, None),
    (WireStatus.BUILDING, ApplicationState.BUILDING),
    (WireStatus.READY, ApplicationState.READY),
    (WireStatus.RESUMABLE, ApplicationState.PAUSED),
    (WireStatus.FAILED, ApplicationState.ERROR),
    (WireStatus.BUSY, ApplicationState.SIMULATING),
    (WireStatus.SAVE_AND_QUIT, ApplicationState.CANCELLING),
])
def test_wire_state_mapping(wire, expected):
    assert application_state_hint(wire) is expected


def test_status_parser_requires_protocol():
    with pytest.raises(ValueError):
        parse_status({"status": "READY"})


def test_status_parser_accepts_success_without_crash():
    parsed = parse_status({"protocol_version": "0.18", "status": "READY"})
    assert parsed.error == ""
    assert parsed.crash_kind == ""


@pytest.mark.parametrize("crash_kind", ["", None])
def test_status_parser_accepts_empty_or_absent_crash_kind(crash_kind):
    response = {"protocol_version": "0.18", "status": "FAILED",
                "error": "solver failed"}
    if crash_kind is not None:
        response["crash_kind"] = crash_kind
    assert parse_status(response).crash_kind == ""


def test_status_parser_preserves_structured_crash_and_multiline_report():
    parsed = parse_status({
        "protocol_version": "0.18", "status": "FAILED",
        "crash_kind": "device_assert",
        "error": "solver failed\nCUDA assertion\nstderr tail",
    })
    assert parsed.crash_kind == "device_assert"
    assert "CUDA assertion\nstderr tail" in parsed.error


def test_status_parser_rejects_non_text_crash_kind():
    with pytest.raises(ValueError, match="crash_kind"):
        parse_status({"protocol_version": "0.18", "status": "FAILED",
                      "crash_kind": 18})
