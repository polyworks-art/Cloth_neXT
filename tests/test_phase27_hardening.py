# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Phase 2.7 hardening acceptance tests."""

import json
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import cloth_next
from cloth_next.core.errors import ClothNextError
from cloth_next.ppf import health as health_module
from cloth_next.ppf.health import HealthSnapshot, start_owned_and_wait
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.updater.install_paths import (ManagedSolverPaths, read_current,
                                              write_current)
from cloth_next.updater.managed import ManagedSolverInstaller
from cloth_next.updater.solver_manifest import SolverCompatibilityEntry
from cloth_next.updater.states import InstallerState

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "cloth_next"


def package_sources():
    return [path for path in PACKAGE_ROOT.rglob("*.py")
            if "__pycache__" not in path.parts]


# --- goal 1: bl_ext namespace safety ------------------------------------------

def test_no_absolute_self_imports_inside_the_package():
    """Absolute `cloth_next.` imports break under bl_ext.<repo>.<extension>."""
    offenders = []
    pattern = re.compile(r"^\s*(from cloth_next[.\s]|import cloth_next)", re.MULTILINE)
    for path in package_sources():
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


# --- goals 2+3: licensing and manifest ----------------------------------------

def test_every_package_source_carries_an_spdx_header():
    missing = [str(path.relative_to(REPO_ROOT)) for path in package_sources()
               if "SPDX-License-Identifier: GPL-3.0-or-later"
               not in path.read_text(encoding="utf-8")]
    assert missing == []


def test_license_file_is_the_full_gpl3_text():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in text
    assert "Version 3, 29 June 2007" in text


def test_manifest_is_limited_to_windows_with_declared_permissions():
    manifest = tomllib.loads(
        (PACKAGE_ROOT / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert manifest["platforms"] == ["windows-x64"]
    assert manifest["license"] == ["SPDX:GPL-3.0-or-later"]
    assert manifest["copyright"]
    assert len(manifest["tagline"]) <= 64 and not manifest["tagline"].endswith(".")
    permissions = manifest["permissions"]
    assert set(permissions) == {"network", "files"}
    for reason in permissions.values():
        assert reason and len(reason) <= 64 and not reason.endswith(".")


# --- goal 6: incompatible solver never reports a successful start --------------

class FakeManager:
    def __init__(self):
        self.stopped = False
        self.config = SimpleNamespace(host="127.0.0.1", port=65000,
                                      connect_timeout=0.05, read_timeout=0.05,
                                      startup_timeout=2.0)

    def executable_version(self):
        return ("0.1.0", "0.99", "1")

    def start(self):
        pass

    def poll(self):
        return SimpleNamespace(running=True, process_id=123,
                               progress=SimpleNamespace(ready=True, tail=()))

    def stop(self):
        self.stopped = True


def incompatible_snapshot():
    from datetime import datetime, timezone
    return HealthSnapshot(
        reachable=True, compatible=False,
        ownership=ConnectionOwnership.OWNED_PROCESS, process_running=True,
        host="127.0.0.1", port=65000, package_version="0.1.0",
        protocol_version="0.99", schema_version="1", wire_status="READY",
        application_state=None, process_id=123, exit_code=None,
        last_error=None, checked_at=datetime.now(timezone.utc))


def test_incompatible_owned_solver_start_raises_and_stops(monkeypatch):
    monkeypatch.setattr(health_module, "port_reachable", lambda *_a, **_k: False)
    monkeypatch.setattr(health_module, "query_health",
                        lambda **_kwargs: incompatible_snapshot())
    manager = FakeManager()
    with pytest.raises(ClothNextError) as excinfo:
        start_owned_and_wait(manager)
    assert manager.stopped
    assert "not compatible" in excinfo.value.record.user_message


# --- goal 7: tampered managed-installation metadata -----------------------------

def make_entry():
    tag = "2026-07-09-04-39"
    asset = f"ppf-contact-solver-{tag}-win64.zip"
    return SolverCompatibilityEntry(
        platform="windows-x86_64", solver_package_version="0.1.0",
        protocol_version="0.11", schema_version="1",
        official_repository="st-tech/ppf-contact-solver",
        official_release_tag=tag, official_asset_name=asset,
        official_asset_url=("https://github.com/st-tech/ppf-contact-solver/"
                            f"releases/download/{tag}/{asset}"),
        download_size=1, sha256="a" * 64, archive_layout_version=1,
        health_check_required=True)


@pytest.mark.parametrize("payload", [
    "not json at all",
    json.dumps(["a", "list"]),
    json.dumps({"active_version": "../escape", "executable": "ppf-cts-server.exe"}),
    json.dumps({"active_version": "0.1.0", "executable": "../../evil.exe"}),
    json.dumps({"active_version": "0.1.0", "executable": "/abs/ppf-cts-server.exe"}),
    json.dumps({"active_version": "0.1.0", "executable": "C:/evil/ppf-cts-server.exe"}),
    json.dumps({"active_version": "0.1.0", "executable": "renamed-server.exe"}),
    json.dumps({"active_version": "0.1.0", "executable": 5}),
])
def test_tampered_current_json_is_rejected(tmp_path, payload):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    paths.current_json.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        read_current(paths)


def test_tampered_metadata_puts_installer_into_repair(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    paths.current_json.write_text(
        json.dumps({"active_version": "0.1.0", "executable": "../../evil.exe"}),
        encoding="utf-8")
    installer = ManagedSolverInstaller(paths, make_entry(),
                                       probe_version=lambda _p: ("0.1.0", "0.11", "1"),
                                       health_check=lambda _p: True,
                                       fetch=lambda *_a, **_k: None)
    assert installer.state is InstallerState.REPAIR_REQUIRED


def test_valid_current_json_still_loads(tmp_path):
    paths = ManagedSolverPaths(tmp_path / "managed")
    paths.root.mkdir(parents=True)
    write_current(paths, "0.1.0", "ppf-cts-server.exe")
    active = read_current(paths)
    assert active.version == "0.1.0"
    expected = (paths.version_dir("0.1.0") / "ppf-cts-server.exe").resolve()
    assert active.executable_path(paths) == expected


def test_executable_path_containment_is_enforced(tmp_path):
    from cloth_next.updater.install_paths import ActiveInstallation
    paths = ManagedSolverPaths(tmp_path / "managed")
    tampered = ActiveInstallation("0.1.0", "sub/../../../ppf-cts-server.exe", "")
    with pytest.raises(ValueError, match="tampered"):
        tampered.executable_path(paths)


# --- goal 8: unregister cleanup -------------------------------------------------

def test_unregister_invokes_preferences_shutdown():
    registration = (PACKAGE_ROOT / "blender" / "registration.py").read_text(encoding="utf-8")
    assert "preferences.shutdown()" in registration
    preferences = (PACKAGE_ROOT / "blender" / "preferences.py").read_text(encoding="utf-8")
    assert "def shutdown(" in preferences
    assert "installer.cancel()" in preferences
    assert "worker.join(" in preferences


# --- goal 9: single canonical version source ------------------------------------

def test_manifest_is_the_only_version_source():
    assert not hasattr(cloth_next, "__version__")
    manifest = tomllib.loads(
        (PACKAGE_ROOT / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert cloth_next.manifest_version() == manifest["version"]
    solver = json.loads(
        (PACKAGE_ROOT / "solver_compatibility.json").read_text(encoding="utf-8"))
    assert solver["cloth_next_version"] == manifest["version"]


# --- goal 4: no bundled-solver architecture -------------------------------------

def test_build_tool_has_no_solver_bundling_capability():
    source = (REPO_ROOT / "tools" / "build_extension.py").read_text(encoding="utf-8")
    assert "with_solver" not in source
    assert "--with-solver" not in source
