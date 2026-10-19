# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""AETHER/GAIA engine identity, migration and transactional GAIA installation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from cloth_next.updater import engines
from cloth_next.updater.gaia_install import GaiaInstallError, install_gaia_package
from cloth_next.updater.install_paths import ManagedSolverPaths
from cloth_next.updater.solver_registry import (SolverInstallation, SolverRegistry,
                                                load_registry, write_registry)

REV = "bf3dad56f7a0" + "0" * 28
BUILD_ID = "gaia-0.26.0-dev.1+cuda12.8.windows-x64.bf3dad56f7a0"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_files(*, worker=b"worker", dll=b"backend") -> dict[str, bytes]:
    files = {
        "target/cuda/release/ppf-cts-server.exe": b"server",
        "target/cuda/release/ppf-contact-solver.exe": worker,
        "bin/libsimbackend_cuda.dll": dll,
        "frontend/_scene_.py": b"scene",
    }
    build = {"kind": engines.GAIA_BUILD_KIND, "version": "0.26.0-dev.1", "build_id": BUILD_ID,
             "source_revision": REV, "platform": "windows-x64", "backend": "cuda12.8",
             "base_release_tag": "2026-10-13-12-32", "protocol_version": "0.26",
             "files": {"target/cuda/release/ppf-contact-solver.exe": _sha(b"worker"),
                       "bin/libsimbackend_cuda.dll": _sha(b"backend")}}
    files["gaia-build.json"] = json.dumps(build).encode()
    files["SHA256SUMS.txt"] = "".join(
        f"{_sha(data)}  {name}\n" for name, data in sorted(files.items())).encode()
    return files


def write_zip(path: Path, files: dict[str, bytes], prefix=BUILD_ID) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(f"{prefix}/{name}" if prefix else name, data)
    return path


def installation(root: Path, *, source="official", managed=True, tag="2026-10-13-12-32",
                 protocol="0.26", installation_id="aether"):
    root.mkdir(parents=True, exist_ok=True)
    exe = root / "ppf-cts-server.exe"
    exe.write_bytes(b"x")
    return SolverInstallation(installation_id, "Gaia 0.26", source, str(root), str(exe),
                              str(root / "frontend"), "0.1.0", protocol, "2", tag, managed,
                              True, True)


@pytest.fixture
def paths(tmp_path):
    return ManagedSolverPaths(tmp_path / "solver")


def probe(_exe):
    return ("0.1.0", "0.26", "2")


def test_official_releases_are_aether_and_unidentified_externals_are_unverified(tmp_path):
    official = installation(tmp_path / "official")
    external = installation(tmp_path / "external", source="external", managed=False, tag=None,
                            installation_id="ext")
    assert engines.engine_of(official) == engines.AETHER
    assert engines.verified_upstream(official)
    assert engines.engine_of(external) == engines.AETHER
    assert not engines.verified_upstream(external)
    assert "unverified" in engines.installation_label(external)


def test_gaia_identity_requires_a_valid_build_file(tmp_path):
    item = installation(tmp_path / "g", source="external", managed=False, tag=None)
    (tmp_path / "g" / engines.GAIA_BUILD_FILE).write_text('{"kind": "other"}', encoding="utf-8")
    assert engines.engine_of(item) == engines.AETHER
    (tmp_path / "g" / engines.GAIA_BUILD_FILE).write_bytes(package_files()["gaia-build.json"])
    assert engines.engine_of(item) == engines.GAIA


@pytest.mark.parametrize("bad", [
    {"files": {"../escape.dll": "0" * 64}},
    {"files": {"C:/abs.dll": "0" * 64}},
    {"source_revision": "not-a-hash"},
    {"build_id": "gaia-1+x.macos.000000000000"},
    {"files": {"bin/x.dll": "short"}},
])
def test_unsafe_or_malformed_build_descriptions_are_rejected(bad):
    payload = json.loads(package_files()["gaia-build.json"])
    payload.update(bad)
    with pytest.raises(ValueError):
        engines.parse_gaia_build(payload)


def test_best_installation_never_mixes_engines(tmp_path):
    aether = installation(tmp_path / "a")
    gaia = installation(tmp_path / "g", source="gaia", tag=None, installation_id="gaia")
    (tmp_path / "g" / engines.GAIA_BUILD_FILE).write_bytes(package_files()["gaia-build.json"])
    registry = SolverRegistry((aether, gaia), "aether")
    assert engines.best_installation(registry, engines.GAIA).installation_id == "gaia"
    assert engines.best_installation(registry, engines.AETHER).installation_id == "aether"
    assert engines.selected_engine(registry) == engines.AETHER


def test_install_is_verified_registered_and_keeps_selection(tmp_path, paths):
    write_registry(paths.registry_json, SolverRegistry((installation(tmp_path / "a"),), "aether"))
    archive = write_zip(tmp_path / "gaia.zip", package_files())
    digest = _sha(archive.read_bytes())
    result = install_gaia_package(archive, paths, probe_version=probe, health_check=lambda _p: True,
                                  expected_sha256=digest, expected_size=archive.stat().st_size,
                                  expected_build_id=BUILD_ID)
    registry = load_registry(paths.registry_json)
    assert registry.selected_installation_id == "aether"  # never switched silently
    gaia = registry.get(result.installation.installation_id)
    assert engines.engine_of(gaia) == engines.GAIA and gaia.managed
    assert list(paths.staging_dir.iterdir()) == []


@pytest.mark.parametrize("mutate,message", [
    (lambda files: files.update({"bin/libsimbackend_cuda.dll": b"tampered"}), "corrupted"),
    (lambda files: files.update({"extra.exe": b"x"}), "unlisted"),
    (lambda files: files.pop("gaia-build.json"), "not a GAIA"),
])
def test_corrupted_packages_change_nothing(tmp_path, paths, mutate, message):
    files = package_files()
    mutate(files)
    archive = write_zip(tmp_path / "bad.zip", files)
    with pytest.raises(GaiaInstallError, match=message):
        install_gaia_package(archive, paths, probe_version=probe, health_check=lambda _p: True)
    assert not paths.registry_json.exists()
    assert list(paths.staging_dir.iterdir()) == []
    assert not any(paths.versions_dir.iterdir())


def test_checksum_size_and_traversal_are_rejected_before_extraction(tmp_path, paths):
    archive = write_zip(tmp_path / "gaia.zip", package_files())
    with pytest.raises(GaiaInstallError, match="SHA-256"):
        install_gaia_package(archive, paths, probe_version=probe, health_check=lambda _p: True,
                             expected_sha256="0" * 64)
    with pytest.raises(GaiaInstallError, match="size"):
        install_gaia_package(archive, paths, probe_version=probe, health_check=lambda _p: True,
                             expected_size=1)
    evil = write_zip(tmp_path / "evil.zip", {"../../evil.txt": b"x"}, prefix="")
    with pytest.raises(GaiaInstallError, match="unpacked safely"):
        install_gaia_package(evil, paths, probe_version=probe, health_check=lambda _p: True)
    assert not (tmp_path / "evil.txt").exists()


def test_failed_health_check_and_protocol_mismatch_install_nothing(tmp_path, paths):
    archive = write_zip(tmp_path / "gaia.zip", package_files())
    with pytest.raises(GaiaInstallError, match="health check"):
        install_gaia_package(archive, paths, probe_version=probe, health_check=lambda _p: False)
    with pytest.raises(GaiaInstallError, match="protocol"):
        install_gaia_package(archive, paths, probe_version=lambda _p: ("0.1.0", "0.22", "2"),
                             health_check=lambda _p: True)
    assert not any(paths.versions_dir.iterdir())


def test_repair_replaces_a_damaged_build_and_reinstall_is_idempotent(tmp_path, paths):
    archive = write_zip(tmp_path / "gaia.zip", package_files())
    first = install_gaia_package(archive, paths, probe_version=probe, health_check=lambda _p: True)
    dll = Path(first.installation.root_path) / "bin/libsimbackend_cuda.dll"
    assert install_gaia_package(archive, paths, probe_version=probe,
                                health_check=lambda _p: True).already_installed
    dll.write_bytes(b"damaged")
    repaired = install_gaia_package(archive, paths, probe_version=probe, health_check=lambda _p: True)
    assert not repaired.already_installed and dll.read_bytes() == b"backend"
    assert len(load_registry(paths.registry_json).installations) == 1


def test_inherited_reproducibility_switches_never_reach_a_solver(tmp_path, monkeypatch):
    from cloth_next.ppf.process import SolverProcessConfig
    monkeypatch.setenv("PPF_REPRO_FAST_CONTACT", "1")
    exe = tmp_path / "server.exe"
    exe.write_bytes(b"x")
    plain = SolverProcessConfig(executable_path=exe, working_directory=tmp_path)
    assert not any(key.startswith("PPF_REPRO_") for key in plain.subprocess_environment())
    gaia = SolverProcessConfig(executable_path=exe, working_directory=tmp_path,
                               environment=engines.REPRODUCIBLE_ENVIRONMENT)
    env = gaia.subprocess_environment()
    assert env["PPF_REPRO_WALK_SPMV"] == "1" and env["PPF_REPRO_FAST_CONTACT"] == "1"


def _cuda_only_tree(root: Path) -> Path:
    for name in ("ppf-cts-server.exe", "ppf-contact-solver.exe"):
        path = root / "target/cuda/release" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return root


def test_cuda_only_gaia_offers_and_resolves_explicit_cuda(tmp_path, monkeypatch):
    """CNX-E132 in 2.9.27: an explicit CUDA choice failed because no CPU server existed."""
    from types import SimpleNamespace
    from cloth_next.ppf import backend_selection
    root = _cuda_only_tree(tmp_path / "gaia")
    assert backend_selection.available_backend_choices("0.26", root) == ("AUTO", "CUDA")
    probe = SimpleNamespace(returncode=0, stdout="backend: cuda\n")
    monkeypatch.setattr(backend_selection.subprocess, "run", lambda *a, **k: probe)
    chosen = backend_selection.executable_for_choice(root, "0.26", "CUDA")
    assert chosen == root / "target/cuda/release/ppf-cts-server.exe"
    backend_selection._preferred_executable_cached.cache_clear()
    assert backend_selection.preferred_executable(root) == chosen
    with pytest.raises(ValueError, match="not available"):
        backend_selection.executable_for_choice(root, "0.26", "CPU")


def test_cuda_only_release_without_working_gpu_says_so(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from cloth_next.ppf import backend_selection
    root = _cuda_only_tree(tmp_path / "gaia")
    monkeypatch.setattr(backend_selection.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=3, stdout=""))
    backend_selection._preferred_executable_cached.cache_clear()
    with pytest.raises(ValueError, match="CUDA only"):
        backend_selection.preferred_executable(root)
    with pytest.raises(ValueError, match="unavailable on this machine"):
        backend_selection.executable_for_choice(root, "0.26", "CUDA")
