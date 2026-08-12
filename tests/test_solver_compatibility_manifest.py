# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from pathlib import Path

import pytest

from cloth_next.updater.solver_manifest import (OFFICIAL_REPOSITORY_SLUG,
                                                download_availability,
                                                load_bundled_manifest,
                                                parse_manifest)

REPO_ROOT = Path(__file__).resolve().parents[1]
PLATFORM = "windows-x86_64"


def valid_payload():
    return json.loads((REPO_ROOT / "cloth_next" / "solver_compatibility.json")
                      .read_text(encoding="utf-8"))


def entry(payload):
    return payload["platforms"][PLATFORM]["releases"][0]


def test_bundled_manifest_is_valid_and_matches_addon_version():
    import tomllib
    manifest_version = tomllib.loads(
        (REPO_ROOT / "cloth_next" / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]
    manifest = load_bundled_manifest(expected_cloth_next_version=manifest_version)
    resolved = manifest.entry_for(PLATFORM)
    assert resolved is not None
    assert resolved.official_repository == OFFICIAL_REPOSITORY_SLUG
    assert resolved.protocol_version == "0.13"
    assert resolved.schema_version == "2"
    assert resolved.download_size > 0
    assert len(resolved.sha256) == 64


def test_valid_manifest_parses():
    manifest = parse_manifest(valid_payload())
    assert manifest.entry_for(PLATFORM).official_release_tag == "2026-07-26-22-53"
    releases = manifest.releases_for(PLATFORM)
    assert [(item.protocol_version, item.schema_version) for item in releases] == [
        ("0.13", "2"), ("0.18", "2")]
    assert releases[1].official_release_tag == "2026-08-12-15-47"
    assert releases[1].download_size == 447922058
    assert releases[1].sha256 == (
        "f80d185b5c585b5f7749d747f317f4e7ab57d0522f10083f970089ff7d378733")


@pytest.mark.parametrize("missing", ["sha256", "protocol_version", "schema_version"])
def test_missing_required_fields_rejected(missing):
    payload = valid_payload()
    del entry(payload)[missing]
    with pytest.raises(ValueError, match=missing):
        parse_manifest(payload)


def test_wrong_repository_owner_rejected():
    payload = valid_payload()
    entry(payload)["official_repository"] = "someone-else/ppf-contact-solver"
    with pytest.raises(ValueError, match="official repository"):
        parse_manifest(payload)


def test_wrong_repository_name_rejected():
    payload = valid_payload()
    entry(payload)["official_repository"] = "st-tech/other-solver"
    with pytest.raises(ValueError, match="official repository"):
        parse_manifest(payload)


def test_unofficial_domain_rejected():
    payload = valid_payload()
    entry(payload)["official_asset_url"] = (
        "https://mirror.evil.net/st-tech/ppf-contact-solver/releases/download/x/y.zip")
    with pytest.raises(ValueError, match="github.com"):
        parse_manifest(payload)


def test_local_path_rejected():
    payload = valid_payload()
    entry(payload)["official_asset_url"] = "C:\\Users\\me\\solver.zip"
    with pytest.raises(ValueError):
        parse_manifest(payload)


def test_file_url_rejected():
    payload = valid_payload()
    entry(payload)["official_asset_url"] = "file:///opt/solver.zip"
    with pytest.raises(ValueError, match="https"):
        parse_manifest(payload)


def test_mutable_latest_url_rejected():
    payload = valid_payload()
    entry(payload)["official_release_tag"] = "latest"
    entry(payload)["official_asset_url"] = (
        "https://github.com/st-tech/ppf-contact-solver/releases/download/latest/x.zip")
    with pytest.raises(ValueError, match="latest"):
        parse_manifest(payload)


def test_url_must_match_pinned_tag_and_asset():
    payload = valid_payload()
    entry(payload)["official_asset_url"] = (
        "https://github.com/st-tech/ppf-contact-solver/releases/download/other-tag/other.zip")
    with pytest.raises(ValueError, match="immutable official release"):
        parse_manifest(payload)


def test_placeholder_values_rejected():
    payload = valid_payload()
    entry(payload)["sha256"] = "VERIFIED_SHA256"
    with pytest.raises(ValueError, match="placeholder"):
        parse_manifest(payload)


def test_malformed_sha256_rejected():
    payload = valid_payload()
    entry(payload)["sha256"] = "abc123"
    with pytest.raises(ValueError, match="64 lowercase hex"):
        parse_manifest(payload)


def test_unknown_manifest_version_rejected():
    payload = valid_payload()
    payload["manifest_version"] = 99
    with pytest.raises(ValueError, match="manifest_version"):
        parse_manifest(payload)


def test_version_mismatch_with_addon_rejected():
    with pytest.raises(ValueError, match="does not match"):
        parse_manifest(valid_payload(), expected_cloth_next_version="9.9.9")


def test_unknown_platform_disables_download():
    resolved, reason = download_availability(valid_payload(), "linux-aarch64")
    assert resolved is None
    assert "linux-aarch64" in reason


def test_invalid_manifest_disables_download_instead_of_guessing():
    payload = valid_payload()
    entry(payload)["sha256"] = "VERIFIED_SHA256"
    resolved, reason = download_availability(payload, PLATFORM)
    assert resolved is None
    assert reason


def test_incompatible_solver_version_is_not_offered():
    """Only the manifest-pinned version may ever be offered as an update."""
    manifest = parse_manifest(valid_payload())
    resolved = manifest.entry_for(PLATFORM)
    assert resolved.solver_package_version == "0.1.0"
    # An unknown installed version is not silently treated as compatible.
    assert resolved.solver_package_version != "99.0.0"
