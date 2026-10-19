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
        ("0.26", "Gaia"),
        ("0.23", "Gaia"),
        ("0.13", "Velune"),
        ("0.18", "Lumen"),
        ("0.22", "Gaia"),
    ]
    assert releases[0].display_name == "Gaia 0.26"
    assert releases[1].display_name == "Gaia 0.23"
    assert all(entry.codename == entry.display_name for entry in releases[2:])


def test_official_releases_are_presented_as_aether_without_mutation(
        blender_env, monkeypatch, tmp_path):
    import cloth_next.blender.solver_release_naming as naming

    old = installation(tmp_path, installation_id="old", display_name="Simulation Solver 2026-07-13",
                       protocol="0.11", schema="1", release_tag="2026-07-13-21-05")
    gaia_upstream = installation(tmp_path, installation_id="gaia22", display_name="Gaia",
                                 protocol="0.22", schema="2", release_tag="2026-09-21-21-32")
    registry = SolverRegistry((old, gaia_upstream), gaia_upstream.installation_id)
    monkeypatch.setattr(naming, "_ORIGINAL_READ_REGISTRY", lambda: (registry, None))

    renamed, error = naming._read_registry_with_release_names()

    assert error is None
    # The historic upstream codename "Gaia" is an official ST-Tech release: AETHER.
    assert [item.display_name for item in renamed.installations] == [
        "AETHER · upstream 2026-07-13-21-05", "AETHER · upstream 2026-09-21-21-32"]
    assert renamed.selected_installation_id == gaia_upstream.installation_id
    assert renamed.installations[1].executable_path == gaia_upstream.executable_path
    assert gaia_upstream.display_name == "Gaia"


def test_external_solver_without_gaia_identity_is_unverified_aether(blender_env, tmp_path):
    import cloth_next.blender.solver_release_naming as naming

    external = installation(tmp_path, installation_id="external", display_name="Custom solver 0.1.0",
                            protocol="0.26", schema="2", release_tag=None)
    assert naming.release_name(external) == "AETHER · external, unverified build (protocol 0.26)"


def test_gaia_build_file_identifies_gaia(blender_env, tmp_path):
    import json
    import cloth_next.blender.solver_release_naming as naming
    from cloth_next.updater import engines

    gaia = installation(tmp_path, installation_id="gaia", display_name="anything",
                        protocol="0.26", schema="2", release_tag=None)
    (Path(gaia.root_path) / engines.GAIA_BUILD_FILE).write_text(json.dumps({
        "kind": engines.GAIA_BUILD_KIND, "version": "0.26.1",
        "build_id": "gaia-0.26.1+cuda12.8.windows-x64.bf3dad5aaaaa",
        "source_revision": "bf3dad5aaaaa", "platform": "windows-x64", "backend": "cuda12.8",
        "base_release_tag": "2026-10-13-12-32", "protocol_version": "0.26",
        "files": {"bin/libsimbackend_cuda.dll": "0" * 64}}), encoding="utf-8")
    assert engines.engine_of(gaia) == engines.GAIA
    assert naming.release_name(gaia) == "GAIA 0.26.1 · build bf3dad5aaaaa"


def test_registration_installs_and_restores_naming_adapter(blender_env):
    import cloth_next.blender.preferences as preferences
    import cloth_next.blender.solver_release_naming as naming

    original = preferences._read_registry
    blender_env.registration.register()
    assert preferences._read_registry is naming._read_registry_with_release_names

    blender_env.registration.unregister()
    assert preferences._read_registry is original
