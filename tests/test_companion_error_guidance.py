# SPDX-License-Identifier: GPL-3.0-or-later

import json

import pytest

from companion.error_guidance import parse_guidance, replace_recommendation


def test_online_guidance_is_validated_and_indexed_by_code():
    payload = json.dumps({
        "schema": 1,
        "errors": [
            {"code": "cnx-e161", "action": "  Lower Friction first.  "},
            {"code": "not-a-code", "action": "Ignore me"},
        ],
    }).encode()
    assert parse_guidance(payload) == {
        "CNX-E161": "Lower Friction first.",
    }


def test_online_guidance_replaces_only_the_recovery_line():
    details = "Stage: Solve\nCause: PCG failed\nWhat to do: Old advice"
    assert replace_recommendation(details, "Lower Friction first.") == (
        "Stage: Solve\nCause: PCG failed\nWhat to do: Lower Friction first.")


def test_unknown_guidance_schema_is_rejected():
    with pytest.raises(ValueError, match="schema"):
        parse_guidance(b'{"schema": 2, "errors": []}')
