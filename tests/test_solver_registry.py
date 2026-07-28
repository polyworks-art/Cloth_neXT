# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import replace

import pytest

from cloth_next.updater.solver_registry import (
    SolverInstallation, SolverRegistry, load_registry,
    official_installation_id, write_registry)


def installation(tmp_path, installation_id, protocol, schema, *,
                 managed=True, healthy=True):
    root = tmp_path / installation_id
    executable = root / "target" / "release" / "ppf-cts-server.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"solver")
    (root / "frontend").mkdir()
    return SolverInstallation(
        installation_id, f"PPF {protocol}", "official" if managed else "external",
        str(root), str(executable), str(root / "frontend"), "0.1.0",
        protocol, schema, "tag" if managed else None, managed, True, healthy,
        "stable")


def test_empty_registry(tmp_path):
    assert load_registry(tmp_path / "registry.json") == SolverRegistry()


def test_register_011_and_013_side_by_side_and_persist(tmp_path):
    first = installation(tmp_path, "official-011-win64", "0.11", "1")
    second = installation(tmp_path, "official-013-win64", "0.13", "2")
    registry = SolverRegistry().register(first).register(second).select(
        second.installation_id)
    write_registry(tmp_path / "registry.json", registry)
    loaded = load_registry(tmp_path / "registry.json")
    assert loaded.installations == (first, second)
    assert loaded.selected == second


def test_external_registration_and_unregister_preserves_files(tmp_path):
    external = installation(
        tmp_path, "external-fixed", "0.13", "2", managed=False)
    registry = SolverRegistry().register(external)
    registry = registry.unregister(external.installation_id)
    assert not registry.installations
    assert external.executable.is_file()


def test_duplicate_id_with_different_installation_rejected(tmp_path):
    first = installation(tmp_path, "same", "0.11", "1")
    with pytest.raises(ValueError, match="already registered"):
        SolverRegistry().register(first).register(
            replace(first, protocol_version="0.13", schema_version="2"))


def test_missing_or_unhealthy_installation_cannot_be_selected(tmp_path):
    missing = replace(
        installation(tmp_path, "missing", "0.11", "1"),
        executable_path=str(tmp_path / "gone.exe"))
    unhealthy = installation(
        tmp_path, "unhealthy", "0.13", "2", healthy=False)
    for item in (missing, unhealthy):
        with pytest.raises(ValueError):
            SolverRegistry().register(item).select(item.installation_id)


def test_missing_selected_id_is_reported_without_fallback(tmp_path):
    first = installation(tmp_path, "first", "0.11", "1")
    registry = replace(
        SolverRegistry().register(first),
        selected_installation_id="missing")
    write_registry(tmp_path / "registry.json", registry)
    loaded = load_registry(tmp_path / "registry.json")
    assert loaded.selected is None
    assert loaded.selected_installation_id == "missing"


def test_corrupt_registry_rejected(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt"):
        load_registry(path)


def test_official_installation_id_is_stable():
    assert official_installation_id("2026-07-26-22-53") == (
        "official-2026-07-26-22-53-win64")
