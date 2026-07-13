# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the immutable solver release identity.

Root cause: update detection compared only the internal solver package
version, and ``current.json`` stored only that version. A new official
release reporting the same internal package version (e.g. ``0.1.0``) was
therefore never offered as an update. A managed installation is now
identified by the immutable official release tag plus the asset SHA-256; the
internal package version stays a compatibility check of the downloaded
executable but never decides update availability alone.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cloth_next.updater.install_paths import (
    CURRENT_METADATA_VERSION, ActiveInstallation, ManagedSolverPaths,
    make_current_record, read_current, validate_installation_id,
    write_current, write_legacy_current)
from cloth_next.updater.managed import ManagedSolverInstaller
from cloth_next.updater.states import InstallerState
from cloth_next.updater.update_check import (UpdateDecision, evaluate_update,
                                             solver_update_available)
from cloth_next.updater.view_model import build_section, InstalledInfo
from cloth_next.updater.modes import InstallationMode

from tests.test_solver_installer_security import (FetchSpy, make_entry,
                                                  make_solver_zip,
                                                  run_pipeline)

TAG = "2026-07-09-04-39"


def v2_record(**overrides):
    fields = dict(
        installation_id=TAG, solver_package_version="0.1.0",
        executable_relative="ppf-cts-server.exe",
        official_release_tag=TAG,
        official_asset_name=f"ppf-contact-solver-{TAG}-win64.zip",
        asset_sha256="a" * 64)
    fields.update(overrides)
    return make_current_record(**fields)


# --- metadata: write/read round trips -----------------------------------------

def test_v2_current_json_round_trip(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    written = write_current(paths, v2_record())
    active = read_current(paths)
    assert active == written
    assert active.metadata_version == CURRENT_METADATA_VERSION
    assert active.installation_id == TAG
    assert active.official_release_tag == TAG
    assert active.asset_sha256 == "a" * 64
    assert active.solver_package_version == "0.1.0"
    assert active.has_release_identity
    payload = json.loads(paths.current_json.read_text(encoding="utf-8"))
    assert payload["metadata_version"] == 2
    assert payload["installation_id"] == TAG


def test_legacy_current_json_stays_readable_and_startable(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    paths.current_json.write_text(json.dumps(
        {"active_version": "0.1.0", "executable": "ppf-cts-server.exe",
         "activated_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
    active = read_current(paths)
    assert active is not None
    assert active.installation_id == "0.1.0"
    assert active.solver_package_version == "0.1.0"
    assert not active.has_release_identity
    assert "Legacy" in active.release_label
    expected = (paths.version_dir("0.1.0") / "ppf-cts-server.exe").resolve()
    assert active.executable_path(paths) == expected


def test_unknown_metadata_version_is_rejected(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    paths.current_json.write_text(json.dumps(
        {"metadata_version": 99, "installation_id": TAG,
         "executable": "ppf-cts-server.exe"}), encoding="utf-8")
    with pytest.raises(ValueError, match="metadata_version"):
        read_current(paths)


@pytest.mark.parametrize("installation_id", [
    "../escape", "a/b", "a\\b", "..", ".hidden", "", " ", "C:evil",
])
def test_installation_id_path_traversal_rejected(installation_id):
    with pytest.raises(ValueError, match="installation id"):
        validate_installation_id(installation_id)


@pytest.mark.parametrize("mutation", [
    {"installation_id": "../escape"},
    {"asset_sha256": "not-hex"},
    {"asset_sha256": "a" * 63},
    {"official_release_tag": ""},
    {"official_asset_name": ""},
    {"executable": "../../evil.exe"},
    {"solver_package_version": ""},
])
def test_tampered_v2_metadata_is_rejected(tmp_path, mutation):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    payload = {"metadata_version": 2, "installation_id": TAG,
               "official_release_tag": TAG,
               "official_asset_name": f"ppf-contact-solver-{TAG}-win64.zip",
               "asset_sha256": "a" * 64, "solver_package_version": "0.1.0",
               "executable": "ppf-cts-server.exe", "activated_at": ""}
    payload.update(mutation)
    paths.current_json.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_current(paths)


def test_incomplete_v2_identity_metadata_is_rejected(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    paths.current_json.write_text(json.dumps(
        {"metadata_version": 2, "installation_id": TAG,
         "executable": "ppf-cts-server.exe"}), encoding="utf-8")
    with pytest.raises(ValueError):
        read_current(paths)


# --- the central update decision ----------------------------------------------

def entry_for_decision(tmp_path, **overrides):
    entry = make_entry(make_solver_zip(tmp_path))
    return replace(entry, **overrides) if overrides else entry


def matching_active(entry):
    return ActiveInstallation(
        metadata_version=2, installation_id=entry.official_release_tag,
        solver_package_version=entry.solver_package_version,
        executable_relative="ppf-cts-server.exe", activated_at="",
        official_release_tag=entry.official_release_tag,
        official_asset_name=entry.official_asset_name,
        asset_sha256=entry.sha256)


def test_same_tag_and_hash_is_no_update(tmp_path):
    entry = entry_for_decision(tmp_path)
    active = matching_active(entry)
    assert evaluate_update(active, entry) is UpdateDecision.UP_TO_DATE
    assert not solver_update_available(active, entry)


def test_same_package_version_different_tag_is_an_update(tmp_path):
    entry = entry_for_decision(tmp_path)
    old_tag = "2025-01-01-00-00"
    asset = f"ppf-contact-solver-{old_tag}-win64.zip"
    active = replace(matching_active(entry), installation_id=old_tag,
                     official_release_tag=old_tag, official_asset_name=asset)
    # identical internal package version — the tag decides
    assert active.solver_package_version == entry.solver_package_version
    assert evaluate_update(active, entry) is UpdateDecision.UPDATE_AVAILABLE
    assert solver_update_available(active, entry)


def test_same_tag_different_hash_is_an_integrity_conflict(tmp_path, caplog):
    entry = entry_for_decision(tmp_path)
    active = replace(matching_active(entry), asset_sha256="b" * 64)
    with caplog.at_level("WARNING"):
        decision = evaluate_update(active, entry)
    assert decision is UpdateDecision.IDENTITY_CONFLICT
    assert solver_update_available(active, entry)  # verified reinstall offered
    assert any("integrity" in record.message for record in caplog.records)


def test_legacy_installation_without_release_id_gets_the_update(tmp_path):
    entry = entry_for_decision(tmp_path)
    legacy = ActiveInstallation(
        metadata_version=1, installation_id="0.1.0",
        solver_package_version=entry.solver_package_version,
        executable_relative="ppf-cts-server.exe", activated_at="")
    assert evaluate_update(legacy, entry) is UpdateDecision.LEGACY_UPDATE_AVAILABLE
    assert solver_update_available(legacy, entry)


def test_no_installation_is_not_an_update(tmp_path):
    entry = entry_for_decision(tmp_path)
    assert evaluate_update(None, entry) is UpdateDecision.NOT_INSTALLED
    assert not solver_update_available(None, entry)


def test_no_manifest_entry_never_claims_an_update():
    legacy = ActiveInstallation(
        metadata_version=1, installation_id="0.1.0",
        solver_package_version="0.1.0",
        executable_relative="ppf-cts-server.exe", activated_at="")
    assert evaluate_update(legacy, None) is UpdateDecision.UP_TO_DATE
    assert not solver_update_available(legacy, None)


# --- installer session initialization (purely local) ---------------------------

def test_legacy_installation_initializes_as_update_available(tmp_path):
    archive = make_solver_zip(tmp_path)
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    write_legacy_current(paths, "0.1.0", "ppf-cts-server.exe")
    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=FetchSpy(archive))
    assert installer.state is InstallerState.UPDATE_AVAILABLE


def test_up_to_date_installation_initializes_as_ready(tmp_path):
    archive = make_solver_zip(tmp_path)
    entry = make_entry(archive)
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    write_current(paths, v2_record(asset_sha256=entry.sha256))
    installer = ManagedSolverInstaller(
        paths, entry, probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=FetchSpy(archive))
    assert installer.state is InstallerState.READY


def test_corrupt_metadata_initializes_as_repair_required(tmp_path):
    archive = make_solver_zip(tmp_path)
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    paths.current_json.write_text("{broken", encoding="utf-8")
    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=FetchSpy(archive))
    assert installer.state is InstallerState.REPAIR_REQUIRED


def test_session_initialization_performs_no_download(tmp_path):
    archive = make_solver_zip(tmp_path)
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    write_legacy_current(paths, "0.1.0", "ppf-cts-server.exe")
    fetch = FetchSpy(archive)
    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=fetch)
    installer.check_for_update()
    assert fetch.calls == 0


# --- cancelled confirmation keeps the update offer -----------------------------

def test_declined_confirmation_returns_to_update_available(tmp_path):
    archive = make_solver_zip(tmp_path)
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    write_legacy_current(paths, "0.1.0", "ppf-cts-server.exe")
    fetch = FetchSpy(archive)
    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=fetch)
    installer.request_download()
    assert installer.state is InstallerState.AWAITING_CONFIRMATION
    assert installer.install(confirmed=False) is InstallerState.UPDATE_AVAILABLE
    assert fetch.calls == 0


def test_cancelled_download_returns_to_update_available(tmp_path):
    from cloth_next.updater.download import DownloadCancelled
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    write_legacy_current(paths, "0.1.0", "ppf-cts-server.exe")

    def cancelling_fetch(_entry, _destination, **_kwargs):
        raise DownloadCancelled("cancelled mid-download")

    installer = ManagedSolverInstaller(
        paths, make_entry(make_solver_zip(tmp_path)),
        probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=cancelling_fetch)
    installer.request_download()
    assert installer.install(confirmed=True) is InstallerState.UPDATE_AVAILABLE
    active = read_current(paths)
    assert active is not None and not active.has_release_identity  # untouched


# --- side-by-side installation and legacy preservation -------------------------

def legacy_setup(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    legacy_dir = paths.version_dir("0.1.0")
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "ppf-cts-server.exe").write_bytes(b"legacy-exe")
    write_legacy_current(paths, "0.1.0", "ppf-cts-server.exe")
    return paths, paths.current_json.read_text(encoding="utf-8")


def test_update_installs_side_by_side_under_the_release_tag(tmp_path):
    paths, _snapshot = legacy_setup(tmp_path)
    archive = make_solver_zip(tmp_path)
    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=FetchSpy(archive))
    assert run_pipeline(installer) is InstallerState.READY
    # old folder untouched, new release side by side under the immutable tag
    assert (paths.version_dir("0.1.0") / "ppf-cts-server.exe").read_bytes() \
        == b"legacy-exe"
    assert (paths.version_dir(TAG) / "ppf-cts-server.exe").is_file()
    active = read_current(paths)
    assert active.metadata_version == CURRENT_METADATA_VERSION
    assert active.installation_id == TAG
    assert active.official_release_tag == TAG
    assert active.solver_package_version == "0.1.0"


@pytest.mark.parametrize("failure", ["hash", "probe", "protocol", "health"])
def test_failed_update_preserves_the_legacy_installation(tmp_path, failure):
    paths, snapshot = legacy_setup(tmp_path)
    archive = make_solver_zip(tmp_path)
    entry = make_entry(archive)
    probe = lambda _p: ("0.1.0", "0.11", "1")  # noqa: E731
    health = lambda _p: True  # noqa: E731
    if failure == "hash":
        entry = replace(entry, sha256="0" * 64)
    elif failure == "probe":
        probe = lambda _p: (_ for _ in ()).throw(OSError("probe crashed"))  # noqa: E731
    elif failure == "protocol":
        probe = lambda _p: ("0.1.0", "0.99", "1")  # noqa: E731
    else:
        health = lambda _p: False  # noqa: E731
    installer = ManagedSolverInstaller(paths, entry, probe_version=probe,
                                       health_check=health, fetch=FetchSpy(archive))
    assert run_pipeline(installer) is InstallerState.ERROR
    # legacy metadata is byte-identical: never destructively migrated
    assert paths.current_json.read_text(encoding="utf-8") == snapshot
    assert (paths.version_dir("0.1.0") / "ppf-cts-server.exe").read_bytes() \
        == b"legacy-exe"
    assert read_current(paths).executable_path(paths).is_file()


def test_successful_update_switches_only_after_health_check(tmp_path):
    paths, _snapshot = legacy_setup(tmp_path)
    archive = make_solver_zip(tmp_path)
    observed = {}

    def health(_executable):
        # at health-check time the legacy installation must still be active
        observed["active_during_health_check"] = read_current(paths)
        return True

    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=health, fetch=FetchSpy(archive))
    assert run_pipeline(installer) is InstallerState.READY
    assert not observed["active_during_health_check"].has_release_identity
    assert read_current(paths).official_release_tag == TAG


def test_reading_legacy_metadata_never_rewrites_it(tmp_path):
    paths, snapshot = legacy_setup(tmp_path)
    archive = make_solver_zip(tmp_path)
    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=FetchSpy(archive))
    installer.check_for_update()
    installer.active_installation()
    assert paths.current_json.read_text(encoding="utf-8") == snapshot


# --- view model: the red alert -------------------------------------------------

def managed_info(release_label):
    return InstalledInfo(InstallationMode.MANAGED_INSTALLATION, "0.1.0",
                         "0.11", "1", release_label=release_label)


def test_update_available_section_carries_the_alert(tmp_path):
    entry = make_entry(make_solver_zip(tmp_path))
    section = build_section(InstallerState.UPDATE_AVAILABLE, entry, None,
                            managed_info("Legacy installation (package 0.1.0)"))
    alert = section.update_alert
    assert alert is not None
    assert alert.title == "Solver Update Available"
    assert any("newer verified PPF Contact Solver" in line for line in alert.lines)
    assert any(line == f"Available: {TAG}" for line in alert.lines)
    assert any("Legacy installation" in line for line in alert.lines)
    assert any("remains active until" in line for line in alert.lines)
    assert alert.action_text == "Install Compatible Solver Update"


def test_ready_section_has_no_alert(tmp_path):
    entry = make_entry(make_solver_zip(tmp_path))
    section = build_section(InstallerState.READY, entry, None,
                            managed_info(TAG))
    assert section.update_alert is None
    assert ("Installed Release", TAG) in section.rows


def test_not_installed_section_has_no_alert(tmp_path):
    entry = make_entry(make_solver_zip(tmp_path))
    section = build_section(InstallerState.NOT_INSTALLED, entry, None, None)
    assert section.update_alert is None


def test_no_verified_download_means_no_alert_and_no_install_action():
    from cloth_next.updater.states import InstallerAction
    section = build_section(InstallerState.UPDATE_AVAILABLE, None,
                            "no verified release", managed_info("x"))
    assert section.update_alert is None
    assert InstallerAction.INSTALL_COMPATIBLE_VERSION not in section.actions


def test_external_installation_gets_no_managed_update_alert(tmp_path):
    entry = make_entry(make_solver_zip(tmp_path))
    info = InstalledInfo(InstallationMode.EXTERNAL_INSTALLATION, "0.1.0",
                         "0.11", "1", release_label="")
    section = build_section(InstallerState.UPDATE_AVAILABLE, entry, None, info)
    assert section.update_alert is None


def test_busy_states_carry_no_alert(tmp_path):
    entry = make_entry(make_solver_zip(tmp_path))
    for state in (InstallerState.DOWNLOADING, InstallerState.VERIFYING,
                  InstallerState.EXTRACTING, InstallerState.INSTALLING,
                  InstallerState.HEALTH_CHECKING,
                  InstallerState.AWAITING_CONFIRMATION):
        section = build_section(state, entry, None, managed_info(TAG))
        assert section.update_alert is None, state
