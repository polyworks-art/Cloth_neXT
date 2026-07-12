# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import shutil
import stat
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from cloth_next.core.state import ApplicationState
from cloth_next.ppf.bootstrap import sha256_file
from cloth_next.ppf.models import ConnectionOwnership, SolverConnection
from cloth_next.updater.addon_update_guard import (can_start_addon_update,
                                                   can_start_solver_update)
from cloth_next.updater.archive import extract_to_staging, inspect_archive
from cloth_next.updater.download import (DownloadCancelled, stream_to_file,
                                         validate_download_url, verify_sha256)
from cloth_next.updater.external import validate_external_installation
from cloth_next.updater.install_paths import (ManagedSolverPaths, read_current,
                                              write_current)
from cloth_next.updater.managed import ManagedSolverInstaller
from cloth_next.updater.modes import InstallationMode, permissions_for
from cloth_next.updater.solver_manifest import (OFFICIAL_DOWNLOAD_PREFIX,
                                                SolverCompatibilityEntry)
from cloth_next.updater.states import DESCRIPTORS, InstallerState
from cloth_next.updater.view_model import build_section

VERSIONS = ("0.1.0", "0.11", "1")


def make_solver_zip(tmp_path, members=None, name="solver.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as bundle:
        for member, data in (members or {"ppf-cts-server.exe": b"exe",
                                         "LICENSES/LICENSE": b"license"}).items():
            bundle.writestr(member, data)
    return path


def make_entry(archive, version="0.1.0"):
    tag = "2026-07-09-04-39"
    asset = f"ppf-contact-solver-{tag}-win64.zip"
    return SolverCompatibilityEntry(
        platform="windows-x86_64", solver_package_version=version,
        protocol_version="0.11", schema_version="1",
        official_repository="st-tech/ppf-contact-solver",
        official_release_tag=tag, official_asset_name=asset,
        official_asset_url=f"{OFFICIAL_DOWNLOAD_PREFIX}{tag}/{asset}",
        download_size=archive.stat().st_size, sha256=sha256_file(archive),
        archive_layout_version=1, health_check_required=True)


class FetchSpy:
    def __init__(self, source):
        self.source = source
        self.calls = 0

    def __call__(self, entry, destination, *, cancel=None, **_kwargs):
        self.calls += 1
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("cancelled")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.source, destination)
        return destination


def make_installer(tmp_path, *, archive=None, entry=None, probe=None,
                   health=None, forbidden=(), running=lambda: False):
    archive = archive or make_solver_zip(tmp_path)
    entry = entry or make_entry(archive)
    fetch = FetchSpy(archive)
    installer = ManagedSolverInstaller(
        ManagedSolverPaths(tmp_path / "managed"), entry,
        probe_version=probe or (lambda _p: VERSIONS),
        health_check=health or (lambda _p: True),
        fetch=fetch, forbidden_roots=tuple(forbidden), is_solver_running=running)
    return installer, fetch


def run_pipeline(installer):
    installer.request_download()
    return installer.install(confirmed=True)


# --- confirmation gating -----------------------------------------------------

def test_download_requires_explicit_user_confirmation(tmp_path):
    installer, fetch = make_installer(tmp_path)
    with pytest.raises(ValueError, match="AWAITING_CONFIRMATION"):
        installer.install(confirmed=True)
    assert fetch.calls == 0


def test_declined_confirmation_performs_no_operation(tmp_path):
    installer, fetch = make_installer(tmp_path)
    installer.request_download()
    state = installer.install(confirmed=False)
    assert state is InstallerState.DOWNLOAD_AVAILABLE
    assert fetch.calls == 0
    assert not (installer.paths.downloads_dir.exists()
                and any(installer.paths.downloads_dir.iterdir()))


def test_no_download_happens_at_construction_time(tmp_path):
    installer, fetch = make_installer(tmp_path)
    build_section(installer.state, None, "no entry", None)
    assert fetch.calls == 0
    assert installer.state is InstallerState.NOT_INSTALLED


# --- download security -------------------------------------------------------

def test_download_url_validation_rejects_http_and_unknown_hosts():
    with pytest.raises(ValueError, match="https"):
        validate_download_url("http://github.com/st-tech/x.zip")
    with pytest.raises(ValueError, match="official"):
        validate_download_url("https://evil.example.com/solver.zip")
    validate_download_url("https://github.com/st-tech/ppf-contact-solver/"
                          "releases/download/t/a.zip")


class FakeResponse:
    def __init__(self, data, content_length=None):
        self.data = data
        self.offset = 0
        self.content_length = content_length

    def read(self, size):
        chunk = self.data[self.offset:self.offset + size]
        self.offset += size
        return chunk

    def getheader(self, _name):
        return self.content_length


def test_content_length_mismatch_rejected(tmp_path):
    response = FakeResponse(b"x" * 10, content_length="999")
    with pytest.raises(ValueError, match="download_size"):
        stream_to_file(response, tmp_path / "f", expected_size=10)


def test_download_size_limit_enforced(tmp_path):
    response = FakeResponse(b"x" * 100)
    with pytest.raises(ValueError, match="limit"):
        stream_to_file(response, tmp_path / "f", expected_size=100, max_size=50)


def test_download_is_cancellable(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        stream_to_file(FakeResponse(b"x" * 10), tmp_path / "f",
                       expected_size=10, cancel=cancel)


def test_cancelled_pipeline_preserves_state(tmp_path):
    archive = make_solver_zip(tmp_path)

    def cancelling_fetch(_entry, _destination, *, cancel=None, **_kwargs):
        # The user cancels while the download is in flight.
        installer.cancel()
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("cancelled mid-download")

    installer = ManagedSolverInstaller(
        ManagedSolverPaths(tmp_path / "managed"), make_entry(archive),
        probe_version=lambda _p: VERSIONS, health_check=lambda _p: True,
        fetch=cancelling_fetch)
    installer.request_download()
    state = installer.install(confirmed=True)
    assert state is InstallerState.DOWNLOAD_AVAILABLE
    assert read_current(installer.paths) is None


# --- hash verification -------------------------------------------------------

def test_hash_mismatch_prevents_installation(tmp_path):
    archive = make_solver_zip(tmp_path)
    entry = replace(make_entry(archive), sha256="0" * 64)
    installer, _ = make_installer(tmp_path, archive=archive, entry=entry)
    assert run_pipeline(installer) is InstallerState.ERROR
    assert "SHA-256" in installer.error.technical_message
    assert read_current(installer.paths) is None
    assert not installer.paths.versions_dir.exists() or \
        not any(installer.paths.versions_dir.iterdir())


def test_verify_sha256_accepts_correct_hash(tmp_path):
    archive = make_solver_zip(tmp_path)
    verify_sha256(archive, sha256_file(archive))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_sha256(archive, "f" * 64)


# --- archive hardening -------------------------------------------------------

def test_path_traversal_rejected(tmp_path):
    archive = make_solver_zip(tmp_path, {"../evil.txt": b"x"})
    with pytest.raises(ValueError, match="traversal"):
        inspect_archive(archive)


@pytest.mark.parametrize("member", ["/etc/passwd", "C:/windows/evil.exe",
                                    "C:\\windows\\evil.exe"])
def test_absolute_archive_paths_rejected(tmp_path, member):
    archive = make_solver_zip(tmp_path, {member: b"x"})
    with pytest.raises(ValueError, match="absolute"):
        inspect_archive(archive)


def test_symlinks_rejected(tmp_path):
    path = tmp_path / "sym.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, "target")
    with pytest.raises(ValueError, match="symbolic link"):
        inspect_archive(path)


def test_reparse_points_rejected(tmp_path):
    path = tmp_path / "reparse.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        info = zipfile.ZipInfo("mount")
        info.external_attr = 0x400
        bundle.writestr(info, "data")
    with pytest.raises(ValueError, match="reparse"):
        inspect_archive(path)


def test_extraction_stays_inside_staging(tmp_path):
    archive = make_solver_zip(tmp_path)
    staging_root = tmp_path / "staging"
    staged = extract_to_staging(archive, staging_root)
    extracted = list(staged.rglob("*"))
    assert extracted
    assert all(staged in path.parents or path == staged for path in extracted)


def test_malicious_archive_fails_whole_pipeline(tmp_path):
    archive = make_solver_zip(tmp_path, {"../../evil.exe": b"x",
                                         "ppf-cts-server.exe": b"exe"})
    installer, _ = make_installer(tmp_path, archive=archive)
    assert run_pipeline(installer) is InstallerState.ERROR
    assert read_current(installer.paths) is None


# --- executable and compatibility gating ------------------------------------

def test_unknown_executable_is_never_started(tmp_path):
    archive = make_solver_zip(tmp_path, {"totally-different.exe": b"exe"})
    probe_calls = []

    def probe(path):
        probe_calls.append(path)
        return VERSIONS

    installer, _ = make_installer(tmp_path, archive=archive, probe=probe)
    assert run_pipeline(installer) is InstallerState.ERROR
    assert probe_calls == []


def test_protocol_mismatch_prevents_activation(tmp_path):
    installer, _ = make_installer(tmp_path, probe=lambda _p: ("0.1.0", "0.99", "1"))
    assert run_pipeline(installer) is InstallerState.ERROR
    assert "protocol" in installer.error.technical_message
    assert read_current(installer.paths) is None


def test_schema_mismatch_prevents_activation(tmp_path):
    installer, _ = make_installer(tmp_path, probe=lambda _p: ("0.1.0", "0.11", "9"))
    assert run_pipeline(installer) is InstallerState.ERROR
    assert "schema" in installer.error.technical_message
    assert read_current(installer.paths) is None


def test_health_check_failure_prevents_activation(tmp_path):
    installer, _ = make_installer(tmp_path, health=lambda _p: False)
    assert run_pipeline(installer) is InstallerState.ERROR
    assert "health check" in installer.error.technical_message
    assert read_current(installer.paths) is None


def test_previous_version_stays_active_on_failed_update(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    old_dir = paths.version_dir("0.0.9")
    old_dir.mkdir(parents=True)
    (old_dir / "ppf-cts-server.exe").write_bytes(b"old")
    write_current(paths, "0.0.9", "ppf-cts-server.exe")

    archive = make_solver_zip(tmp_path)
    installer = ManagedSolverInstaller(
        paths, make_entry(archive), probe_version=lambda _p: VERSIONS,
        health_check=lambda _p: False, fetch=FetchSpy(archive))
    assert installer.state is InstallerState.READY
    installer._state = InstallerState.UPDATE_AVAILABLE  # noqa: SLF001 — direct setup
    installer.request_download()
    assert installer.install(confirmed=True) is InstallerState.ERROR
    active = read_current(paths)
    assert active is not None and active.version == "0.0.9"
    assert (old_dir / "ppf-cts-server.exe").read_bytes() == b"old"


def test_successful_pipeline_activates_version(tmp_path):
    installer, fetch = make_installer(tmp_path)
    assert run_pipeline(installer) is InstallerState.READY
    active = read_current(installer.paths)
    assert active is not None and active.version == "0.1.0"
    assert active.executable_path(installer.paths).is_file()
    assert fetch.calls == 1


def test_never_installs_in_place_over_existing_version(tmp_path):
    installer, _ = make_installer(tmp_path)
    installer.paths.version_dir("0.1.0").mkdir(parents=True)
    assert run_pipeline(installer) is InstallerState.ERROR
    assert "never install in place" in installer.error.technical_message


# --- managed vs. external vs. server boundaries ------------------------------

def test_managed_installation_can_be_removed(tmp_path):
    installer, _ = make_installer(tmp_path)
    run_pipeline(installer)
    installer.remove("0.1.0")
    assert read_current(installer.paths) is None
    assert not installer.paths.version_dir("0.1.0").exists()
    assert installer.state is InstallerState.NOT_INSTALLED


def test_remove_refuses_paths_outside_managed_root(tmp_path):
    installer, _ = make_installer(tmp_path)
    with pytest.raises(ValueError):
        installer.remove("../../outside")


def test_remove_refuses_while_solver_runs(tmp_path):
    installer, _ = make_installer(tmp_path, running=lambda: True)
    with pytest.raises(ValueError, match="stop the solver"):
        installer.remove("0.1.0")


def test_external_installation_is_never_modified(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "ppf-cts-server.exe").write_bytes(b"exe")
    entry = make_entry(make_solver_zip(tmp_path))
    before = {path: path.stat().st_mtime_ns for path in external.rglob("*")}
    result = validate_external_installation(external, lambda _p: VERSIONS, entry)
    after = {path: path.stat().st_mtime_ns for path in external.rglob("*")}
    assert before == after
    assert result.compatible
    permissions = permissions_for(InstallationMode.EXTERNAL_INSTALLATION)
    assert not permissions.may_modify_files
    assert not permissions.may_remove
    assert not permissions.may_update


def test_external_server_is_never_terminated():
    connection = SolverConnection(ConnectionOwnership.EXTERNAL_SERVER)
    assert not connection.may_terminate_process
    permissions = permissions_for(InstallationMode.EXTERNAL_SERVER)
    assert not permissions.may_stop_external_process
    assert not permissions.may_stop_started_process
    assert not permissions.may_start_process
    assert not permissions.may_update


# --- extension directory isolation -------------------------------------------

def test_managed_root_inside_extension_is_rejected(tmp_path):
    extension = tmp_path / "extension"
    extension.mkdir()
    archive = make_solver_zip(tmp_path)
    with pytest.raises(ValueError, match="outside"):
        ManagedSolverInstaller(
            ManagedSolverPaths(extension / "solver"), make_entry(archive),
            probe_version=lambda _p: VERSIONS, health_check=lambda _p: True,
            fetch=FetchSpy(archive), forbidden_roots=(extension,))


def test_install_never_writes_into_extension_directory(tmp_path):
    extension = tmp_path / "extension"
    (extension / "ppf").mkdir(parents=True)
    (extension / "__init__.py").write_text("", encoding="utf-8")
    installer, _ = make_installer(tmp_path, forbidden=(extension,))
    snapshot = sorted(str(path) for path in extension.rglob("*"))
    assert run_pipeline(installer) is InstallerState.READY
    assert sorted(str(path) for path in extension.rglob("*")) == snapshot


def test_addon_update_does_not_delete_managed_solver(tmp_path):
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "__init__.py").write_text("", encoding="utf-8")
    installer, _ = make_installer(tmp_path, forbidden=(extension,))
    run_pipeline(installer)
    # Simulate a Blender extension update: the extension tree is replaced.
    shutil.rmtree(extension)
    extension.mkdir()
    (extension / "__init__.py").write_text("# new version", encoding="utf-8")
    active = read_current(installer.paths)
    assert active is not None
    assert active.executable_path(installer.paths).is_file()


# --- update guards ------------------------------------------------------------

@pytest.mark.parametrize("state", [ApplicationState.STARTING, ApplicationState.READY,
                                   ApplicationState.TRANSFERRING,
                                   ApplicationState.BUILDING,
                                   ApplicationState.SIMULATING,
                                   ApplicationState.FETCHING_FRAMES,
                                   ApplicationState.CANCELLING])
def test_updates_blocked_during_active_solver_work(state):
    assert not can_start_addon_update(state)
    assert not can_start_solver_update(state)


@pytest.mark.parametrize("state", [ApplicationState.NOT_INSTALLED,
                                   ApplicationState.STOPPED, ApplicationState.ERROR])
def test_updates_allowed_when_solver_is_stopped(state):
    assert can_start_addon_update(state)
    assert can_start_solver_update(state)


# --- installer state coverage --------------------------------------------------

def test_every_installer_state_has_a_descriptor():
    for state in InstallerState:
        descriptor = DESCRIPTORS[state]
        assert descriptor.ui_message
        assert descriptor.recommended_action


def test_check_for_update_offers_only_manifest_versions(tmp_path):
    archive = make_solver_zip(tmp_path)
    entry = make_entry(archive, version="0.2.0")
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.ensure_layout()
    paths.version_dir("0.1.0").mkdir(parents=True)
    write_current(paths, "0.1.0", "ppf-cts-server.exe")
    installer = ManagedSolverInstaller(paths, entry,
                                       probe_version=lambda _p: VERSIONS,
                                       health_check=lambda _p: True,
                                       fetch=FetchSpy(archive))
    assert installer.check_for_update() is InstallerState.UPDATE_AVAILABLE
