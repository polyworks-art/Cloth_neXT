# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Strict version parser tests (policy section 3); pure Python, no bpy."""

import pytest

from cloth_next.updater.addon_versions import AddonVersion, parse_version


@pytest.mark.parametrize("text,expected", [
    ("0.1.0", AddonVersion(0, 1, 0)),
    ("0.2.0-beta.1", AddonVersion(0, 2, 0, "beta", 1)),
    ("0.3.0-rc.2", AddonVersion(0, 3, 0, "rc", 2)),
    ("1.0.0", AddonVersion(1, 0, 0)),
    ("10.20.30", AddonVersion(10, 20, 30)),
    ("1.0.0-beta.10", AddonVersion(1, 0, 0, "beta", 10)),
])
def test_accepted_version_formats(text, expected):
    assert parse_version(text) == expected
    assert str(parse_version(text)) == text


@pytest.mark.parametrize("text", [
    "", "1", "1.2", "1.2.3.4", "01.2.3", "1.02.3", "1.2.03",   # structure/leading zeros
    "v1.2.3", "1.2.3-alpha.1", "1.2.3-beta", "1.2.3-beta.0",   # names/zero prerelease
    "1.2.3-beta.01", "1.2.3-rc", "1.2.3+build.5", "1.2.3-beta.1+meta",
    "1.2.3-BETA.1", "1.2.3-rc.1.2", "1.2.3-dev.1", "latest", "1.2.3 beta.1",
])
def test_rejected_malformed_versions(text):
    with pytest.raises(ValueError):
        parse_version(text)


def test_non_string_is_rejected():
    with pytest.raises(ValueError):
        parse_version(None)


def test_beta_rc_stable_ordering():
    beta1 = parse_version("0.3.0-beta.1")
    beta2 = parse_version("0.3.0-beta.2")
    rc1 = parse_version("0.3.0-rc.1")
    stable = parse_version("0.3.0")
    next_beta = parse_version("0.3.1-beta.1")
    assert beta1 < beta2 < rc1 < stable < next_beta
    assert parse_version("0.2.9") < beta1
    assert parse_version("1.0.0-rc.9") < parse_version("1.0.0")
    assert parse_version("0.10.0") > parse_version("0.9.9")


def test_is_prerelease():
    assert parse_version("0.2.0-beta.1").is_prerelease
    assert parse_version("0.2.0-rc.1").is_prerelease
    assert not parse_version("0.2.0").is_prerelease


def test_equality_and_ordering_consistency():
    assert parse_version("1.2.3-rc.4") == AddonVersion(1, 2, 3, "rc", 4)
    assert not (parse_version("1.2.3") < parse_version("1.2.3"))
    assert parse_version("1.2.3") <= parse_version("1.2.3")
