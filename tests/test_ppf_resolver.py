# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

from cloth_next.ppf.layout import BundledSolverLayout
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.resolver import (DEVELOPMENT_EXECUTABLE_ENV, SolverMode,
                                     SolverResolutionContext, SolverResolver,
                                     development_executable_from_environment)
from cloth_next.updater.solver_registry import SolverInstallation


def install_fake(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "ppf-cts-server.exe").write_bytes(b"exe")


def resolver():
    return SolverResolver(lambda _path: ("0.1.0", "0.11", "1"))


def test_priority_external_then_managed_then_development(tmp_path):
    external = tmp_path / "external"
    managed = tmp_path / "managed" / "versions" / "0.1.0"
    development = tmp_path / "dev"
    for root in (external, managed, development):
        install_fake(root)
    context = SolverResolutionContext(external, managed,
                                      development / "ppf-cts-server.exe", True)
    assert resolver().resolve(context).mode is SolverMode.EXTERNAL_INSTALLATION
    assert resolver().resolve(SolverResolutionContext(None, managed,
        development / "ppf-cts-server.exe", True)).mode is SolverMode.MANAGED_INSTALLATION
    assert resolver().resolve(SolverResolutionContext(None, None,
        development / "ppf-cts-server.exe", True)).mode is SolverMode.DEVELOPMENT


def test_external_server_and_missing_solver(tmp_path):
    external = resolver().resolve(SolverResolutionContext(
        external_server_available=True))
    assert external.mode is SolverMode.EXTERNAL_SERVER
    assert external.ownership is ConnectionOwnership.EXTERNAL_SERVER
    assert external.executable_path is None
    assert resolver().resolve(SolverResolutionContext()) is None


def test_no_implicit_extension_or_repository_scanning():
    """The bundled-solver modes are gone; nothing resolves without explicit context."""
    assert {mode.name for mode in SolverMode} == {
        "MANAGED_INSTALLATION", "EXTERNAL_INSTALLATION", "EXTERNAL_SERVER",
        "DEVELOPMENT"}
    assert resolver().resolve(SolverResolutionContext()) is None


def test_external_installation_is_never_writable(tmp_path):
    external = tmp_path / "readonly"
    install_fake(external)
    result = resolver().resolve(SolverResolutionContext(external_path=external))
    assert result.mode is SolverMode.EXTERNAL_INSTALLATION
    assert not result.writable


def test_development_executable_from_environment(monkeypatch, tmp_path):
    monkeypatch.delenv(DEVELOPMENT_EXECUTABLE_ENV, raising=False)
    assert development_executable_from_environment() is None
    monkeypatch.setenv(DEVELOPMENT_EXECUTABLE_ENV, str(tmp_path / "ppf-cts-server.exe"))
    assert development_executable_from_environment() == tmp_path / "ppf-cts-server.exe"


def test_layout_runtime_environment_does_not_write_bundle(tmp_path):
    install_fake(tmp_path)
    (tmp_path / "python").mkdir()
    layout = BundledSolverLayout.from_root(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    environment = dict(layout.process_environment())
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert before == after
    assert environment["PYTHONPATH"] == str(tmp_path.resolve())


def test_selected_installation_routes_executable_and_protocol_together(tmp_path):
    root = tmp_path / "ppf013"
    executable = root / "ppf-cts-server.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"solver")
    frontend = root / "frontend"
    frontend.mkdir()
    installation = SolverInstallation(
        "official-013-win64", "PPF 0.13", "official",
        str(root), str(executable), str(frontend), "0.1.0", "0.13", "2",
        "2026-07-26-22-53", True, True, True, "current")

    result = resolver().resolve(SolverResolutionContext(
        selected_installation=installation))

    assert result is not None
    assert result.installation is installation
    assert result.executable_path == executable
    assert result.frontend_path == frontend
    assert result.protocol_profile.protocol_version == "0.13"
    assert result.protocol_profile.schema_version == "2"
