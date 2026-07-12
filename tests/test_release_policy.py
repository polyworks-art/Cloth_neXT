import json
import zipfile
from pathlib import Path

import pytest

from tools.release_metadata import write_metadata
from tools.scan_release_artifact import scan_names, scan_zip
from tools.validate_release_policy import (check_channel, check_channel_separation,
                                           check_release_manifest, check_sha256sums,
                                           check_tag_matches_manifest, check_zip,
                                           expected_zip_name, parse_version,
                                           tag_to_version)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOLVER_MANIFEST = (REPO_ROOT / "cloth_next" / "solver_compatibility.json").read_text(
    encoding="utf-8")


def make_repo(tmp_path, version="0.2.0"):
    (tmp_path / "cloth_next").mkdir(exist_ok=True)
    (tmp_path / "cloth_next" / "blender_manifest.toml").write_text(
        f'id = "cloth_next"\nversion = "{version}"\nblender_version_min = "5.0.0"\n',
        encoding="utf-8")
    manifest = json.loads(SOLVER_MANIFEST)
    manifest["cloth_next_version"] = version
    (tmp_path / "cloth_next" / "solver_compatibility.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return tmp_path


def make_zip(tmp_path, version="0.2.0", extra=(), name=None):
    manifest = json.loads(SOLVER_MANIFEST)
    manifest["cloth_next_version"] = version
    path = tmp_path / (name or expected_zip_name(parse_version(version)))
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("blender_manifest.toml",
                        f'id = "cloth_next"\nversion = "{version}"\n')
        bundle.writestr("__init__.py", "")
        bundle.writestr("solver_compatibility.json", json.dumps(manifest))
        for member in extra:
            bundle.writestr(member, b"data")
    return path


@pytest.mark.parametrize("text", ["0.2.0", "0.2.1", "1.0.0", "0.3.0-beta.1",
                                  "0.3.0-rc.1"])
def test_semver_accepts_release_and_prerelease(text):
    parse_version(text)


@pytest.mark.parametrize("text", ["0.2", "v0.2.0", "0.2.0-alpha.1", "0.2.0-beta",
                                  "0.02.0", "0.2.0+build", "latest", ""])
def test_semver_rejects_invalid_versions(text):
    with pytest.raises(ValueError):
        parse_version(text)


def test_channel_derivation_and_stable_prerelease_rejection():
    assert parse_version("0.2.0").channel == "stable"
    assert parse_version("0.3.0-beta.2").channel == "beta"
    assert parse_version("0.3.0-rc.1").channel == "beta"
    with pytest.raises(ValueError):
        check_channel(parse_version("0.3.0-beta.1"), "stable")
    with pytest.raises(ValueError):
        check_channel(parse_version("0.3.0"), "beta")


def test_tag_manifest_match_and_mismatch(tmp_path):
    repo = make_repo(tmp_path, "0.2.0")
    assert check_tag_matches_manifest("v0.2.0", repo).text == "0.2.0"
    with pytest.raises(ValueError):
        check_tag_matches_manifest("v0.2.1", repo)
    with pytest.raises(ValueError):
        check_tag_matches_manifest("0.2.0", repo)


def test_zip_name_must_match_version(tmp_path):
    version = parse_version("0.2.0")
    wrong = make_zip(tmp_path, "0.2.0", name="cloth_next-0.9.9-windows-x64.zip")
    with pytest.raises(ValueError, match="ZIP name"):
        check_zip(wrong, version)
    check_zip(make_zip(tmp_path, "0.2.0"), version)


def test_zip_manifest_version_mismatch_rejected(tmp_path):
    path = tmp_path / expected_zip_name(parse_version("0.2.0"))
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("blender_manifest.toml", 'id = "cloth_next"\nversion = "0.1.0"\n')
        bundle.writestr("solver_compatibility.json", SOLVER_MANIFEST)
    with pytest.raises(ValueError, match="manifest version"):
        check_zip(path, parse_version("0.2.0"))


@pytest.mark.parametrize("member", [
    "ppf-cts-server.exe",
    "bin/ppf-contact-solver.exe",
    "ppf-contact-solver-2026-07-09-04-39-win64.zip",
    "solver/windows-x86_64/README.txt",
    "downloads/archive.bin",
    "managed_solver/current.json",
    "headless.bat",
    "nested/start.bat",
    "runtime/vendor.dll",
])
def test_zip_with_forbidden_solver_material_rejected(tmp_path, member):
    bad = make_zip(tmp_path, "0.2.0", extra=(member,))
    with pytest.raises(ValueError, match="forbidden solver material"):
        check_zip(bad, parse_version("0.2.0"))
    assert scan_zip(bad)


def test_scanner_reports_clean_names():
    assert scan_names(["__init__.py", "ppf/transport.py",
                       "solver_compatibility.json"]) == []


def test_release_manifest_checks(tmp_path):
    repo = make_repo(tmp_path, "0.2.0")
    zip_path = make_zip(tmp_path, "0.2.0")
    manifest_path, sums_path = write_metadata(repo, zip_path, tmp_path,
                                              tag="v0.2.0", commit="abc123")
    version = parse_version("0.2.0")
    check_release_manifest(manifest_path, zip_path, version, "v0.2.0")
    check_sha256sums(sums_path, zip_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["solver_bundled"] is False
    assert payload["required_ppf_protocol"] == "0.11"

    payload["solver_bundled"] = True
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="solver_bundled"):
        check_release_manifest(manifest_path, zip_path, version, "v0.2.0")


def test_release_manifest_hash_mismatch_rejected(tmp_path):
    repo = make_repo(tmp_path, "0.2.0")
    zip_path = make_zip(tmp_path, "0.2.0")
    manifest_path, sums_path = write_metadata(repo, zip_path, tmp_path,
                                              tag="v0.2.0", commit="abc123")
    with zipfile.ZipFile(zip_path, "a") as bundle:
        bundle.writestr("extra.py", "changed")
    with pytest.raises(ValueError, match="sha256"):
        check_release_manifest(manifest_path, zip_path, parse_version("0.2.0"), "v0.2.0")
    with pytest.raises(ValueError, match="hash mismatch"):
        check_sha256sums(sums_path, zip_path)


def test_stable_and_beta_channels_stay_separated(tmp_path):
    site = tmp_path / "site"
    (site / "stable").mkdir(parents=True)
    (site / "beta").mkdir(parents=True)
    make_zip(site / "beta", "0.3.0-beta.1")
    check_channel_separation(site, parse_version("0.3.0-beta.1"))
    make_zip(site / "stable", "0.3.0-beta.1")
    with pytest.raises(ValueError, match="stable channel"):
        check_channel_separation(site, parse_version("0.3.0-beta.1"))


def test_stable_index_must_not_reference_prereleases(tmp_path):
    site = tmp_path / "site"
    (site / "stable").mkdir(parents=True)
    (site / "stable" / "index.json").write_text(
        json.dumps({"data": [{"id": "cloth_next", "version": "0.3.0-beta.1"}]}),
        encoding="utf-8")
    with pytest.raises(ValueError, match="prerelease"):
        check_channel_separation(site, parse_version("0.3.0-beta.1"))


def test_tag_requires_v_prefix():
    assert tag_to_version("v1.0.0").text == "1.0.0"
    with pytest.raises(ValueError):
        tag_to_version("1.0.0")
