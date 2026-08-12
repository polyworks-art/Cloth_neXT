# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cloth_next.core.errors import ErrorCategory
from cloth_next.ppf.compatibility import parse_executable_version, validate_versions


def test_pinned_version_output_parses():
    assert parse_executable_version(
        "ppf-cts-server 0.1.0 (protocol v0.18, schema v2)"
    ) == ("0.1.0", "0.18", "2")


def test_exact_protocol_and_schema_match():
    assert validate_versions("0.13", "2", "0.1.0").fully_compatible


def test_current_protocol_profile_is_explicitly_supported():
    from cloth_next.ppf.compatibility import protocol_profile

    profile = protocol_profile("0.18", "2")
    assert profile is not None
    assert validate_versions("0.18", "2", "0.1.0", profile=profile).fully_compatible


def test_retired_protocol_is_rejected():
    from cloth_next.ppf.compatibility import protocol_profile

    assert protocol_profile("0.11", "1") is None
    assert not validate_versions("0.11", "1", "0.1.0").fully_compatible


@pytest.mark.parametrize("protocol,schema", [("0.11", "1"), ("0.13", "1"),
                                              ("0.19", "2")])
def test_mismatch_is_protocol_compatibility_error(protocol, schema):
    result = validate_versions(protocol, schema, "0.1.0")
    assert not result.fully_compatible
    assert result.error.category is ErrorCategory.PROTOCOL_COMPATIBILITY


def test_remote_schema_unknown_is_not_fully_compatible():
    result = validate_versions("0.13", None, None)
    assert result.protocol_compatible
    assert not result.fully_compatible

