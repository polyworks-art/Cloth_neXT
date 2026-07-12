# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from pathlib import Path

from cloth_next.ppf.compatibility import parse_executable_version
from cloth_next.ppf.status import parse_status
from cloth_next.ppf.transport import status_request_bytes


ROOT = Path(__file__).parent / "fixtures" / "ppf_0_11"


def test_request_golden_fixtures_match_encoder():
    expected = status_request_bytes("demo")
    assert (ROOT / "compatibility_request.bin").read_bytes() == expected
    assert (ROOT / "status_request.bin").read_bytes() == expected


def test_response_golden_fixtures_parse():
    for name in ("compatibility_response.json", "status_no_data_response.json", "status_ready_response.json"):
        parsed = parse_status(json.loads((ROOT / name).read_text(encoding="utf-8")))
        assert parsed.protocol_version == "0.11"


def test_version_fixture_matches_pinned_baseline():
    text = (ROOT / "executable_version.txt").read_text(encoding="utf-8")
    assert parse_executable_version(text) == ("0.1.0", "0.11", "1")
