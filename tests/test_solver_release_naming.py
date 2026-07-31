# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for product-facing solver release codenames."""

from __future__ import annotations

from pathlib import Path

from cloth_next.updater.solver_manifest import load_bundled_manifest
from cloth_next.updater.solver_registry import SolverInstallation, SolverRegistry

PLATFORM = "windows-x86_64"


def installation(tmp_path: Path, *, installation_id: str, display_name: str,
                 protocol: str, schema: str, release_tag: str | None):
    root = tmp_path / installation_id
    root.mkdir()
    executable = root / "ppf-cts-server.exe"
    executable.write_bytes(b"solver")
    return SolverInstallation(
        installation_id=installation_id,
        display_name=display_name,
        source="official" if release_tag else "external",
        root_path=str(root),
        executable_path=str(executable),
        frontend_path=str(root / "frontend"),
        package_version="0.1.0",
        protocol_version=protocol,
        schema_version=schema,
        official_release_tag=release_tag,
        managed=release_tag is not None,
        verified=True,
        healthy=True,
        channel="stable",
    )


def test_bundled_releases_have_expected_codenames():
    releases = load_bundled_manifest().releases_for(PLATFORM)
    assert [(entry.protocol_version, entry.release_name) for entry in releases] == [
        ("0.11", "Lunelle"),
        ("0.13", "Velune"),
    ]
    assert all(entry.codename == entry.display_name for entry in releases)


def test_existing_date_named_registry_is_presented_with_codenames(
        blender_env, monkeypatch, tmp_path):
    import cloth_next.blender.solver_release_naming as naming

    lunelle = installation(
        tmp_path,
        installation_id="old",
        display_name="PPF Contact Solver 2026-07-13",
        protocol="0.11",
        schema="1",
        release_tag="2026-07-13-21-05",
    )
    velune = installation(
        tmp_path,
        installation_id="current",
        display_name="PPF Contact Solver 2026-07-26",
        protocol="0.13",
        schema="2",
        release_tag="2026-07-26-22-53",
    )
    registry = SolverRegistry((lunelle, velune), velune.installation_id)
    monkeypatch.setattr(naming, "_ORIGINAL_READ_REGISTRY",
                        lambda: (registry, None))

    renamed, error = naming._read_registry_with_release_names()

    assert error is None
    assert [item.display_name for item in renamed.installations] == [
        "Lunelle", "Velune"]
    assert renamed.selected_installation_id == velune.installation_id
    assert renamed.installations[1].executable_path == velune.executable_path


def test_external_compatible_solver_uses_protocol_codename(
        blender_env, monkeypatch, tmp_path):
    import cloth_next.blender.solver_release_naming as naming

    external = installation(
        tmp_path,
        installation_id="external",
        display_name="Custom PPF 0.1.0",
        protocol="0.13",
        schema="2",
        release_tag=None,
    )
    registry = SolverRegistry((external,), external.installation_id)
    monkeypatch.setattr(naming, "_ORIGINAL_READ_REGISTRY",
                        lambda: (registry, None))

    renamed, _error = naming._read_registry_with_release_names()

    assert renamed.selected.display_name == "Velune"
    assert renamed.selected.managed is False


def test_unknown_solver_keeps_stored_name(blender_env, tmp_path):
    import cloth_next.blender.solver_release_naming as naming

    unknown = installation(
        tmp_path,
        installation_id="unknown",
        display_name="Custom Experimental Solver",
        protocol="9.9",
        schema="99",
        release_tag=None,
    )
    assert naming.release_name(unknown) == "Custom Experimental Solver"


def test_registration_installs_and_restores_naming_adapter(blender_env):
    import cloth_next.blender.preferences as preferences
    import cloth_next.blender.solver_release_naming as naming

    original = preferences._read_registry
    blender_env.registration.register()
    assert preferences._read_registry is naming._read_registry_with_release_names

    blender_env.registration.unregister()
    assert preferences._read_registry is original
