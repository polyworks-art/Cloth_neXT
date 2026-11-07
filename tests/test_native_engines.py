# SPDX-License-Identifier: GPL-3.0-or-later
"""AETHER and TETHYS installed from the server instead of the add-on ZIP."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import zipfile

import pytest

from cloth_next.updater import native_engines
from cloth_next.updater.native_engines import EngineInstallError

ROOT = Path(__file__).resolve().parents[1]
AETHER_EXE = ROOT / "vendor" / "aether" / "windows-x64" / "AETHER Engine.exe"
TETHYS_EXE = ROOT / "vendor" / "tethys" / "windows-x64" / "TETHYS Engine.exe"
windows_only = pytest.mark.skipif(os.name != "nt", reason="runs the vendored Windows engine builds")


def _vendor(engine):
    return json.loads((ROOT / "vendor" / engine / "ENGINE.json").read_text("utf-8"))


def _aether_package(tmp_path, *, name="aether.zip", api=None, binary=AETHER_EXE, **override):
    from cloth_next.aether_engine import client
    vendor = _vendor("aether")
    values = dict(version=vendor["engine_version"], build_id=vendor["build_id"], commit=vendor["engine_commit"])
    values.update(override)
    extra = {"protocol": client.PROTOCOL_VERSION, "api": list(api or client.API_VERSION)}
    path = tmp_path / name
    native_engines.build_package("aether", binary, path, platform="windows-x64", extra=extra, **values)
    return path


def _install_aether(archive, root, **kwargs):
    from cloth_next.aether_engine import engine
    return native_engines.install_package("aether", archive, compatible=engine.package_compatible,
                                          probe=engine.package_probe, root=root, platform="windows-x64",
                                          **kwargs)


@pytest.fixture
def engines_root(tmp_path, monkeypatch):
    root = tmp_path / "engines"
    monkeypatch.setenv(native_engines.ROOT_ENV, str(root))
    monkeypatch.delenv("CLOTH_NEXT_AETHER_ENGINE", raising=False)
    monkeypatch.delenv("CLOTH_NEXT_TETHYS_ENGINE", raising=False)
    return root


@windows_only
def test_aether_installs_outside_the_addon_and_locate_uses_it(tmp_path, engines_root):
    from cloth_next.aether_engine import engine
    archive = _aether_package(tmp_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    result = _install_aether(archive, engines_root, expected_sha256=digest,
                             expected_size=archive.stat().st_size, server_build_id="aether-build-1")
    assert not result.already_installed
    installed = native_engines.installed("aether", platform="windows-x64")
    assert installed.server_build_id == "aether-build-1"
    assert engines_root in installed.binary.parents
    path, override = engine.locate()
    assert path == installed.binary and not override
    identity = engine.identify()
    assert identity.build_id == _vendor("aether")["build_id"]
    # Installing the same build again is a no-op that keeps the engine in place.
    again = _install_aether(archive, engines_root, server_build_id="aether-build-1")
    assert again.already_installed and native_engines.installed("aether").binary == installed.binary


@windows_only
def test_bad_downloads_change_nothing(tmp_path, engines_root):
    good = _aether_package(tmp_path)
    _install_aether(good, engines_root, server_build_id="one")
    before = native_engines.installed("aether")
    with pytest.raises(EngineInstallError, match="SHA-256"):
        _install_aether(good, engines_root, expected_sha256="0" * 64)
    with pytest.raises(EngineInstallError, match="does not match this Cloth NeXt version"):
        _install_aether(_aether_package(tmp_path, name="api.zip", api=[9, 9]), engines_root)
    with pytest.raises(EngineInstallError, match="does not match its description"):
        _install_aether(_aether_package(tmp_path, name="lie.zip", build_id="f" * 16), engines_root)
    # an extra file in the package
    extra = tmp_path / "extra.zip"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(extra, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr("bin/evil.dll", b"x")
    with pytest.raises(EngineInstallError, match="unexpected files"):
        _install_aether(extra, engines_root)
    # a binary that does not match engine.json
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            data = source.read(item)
            target.writestr(item, data + b"!" if item.filename.startswith("bin/") else data)
    with pytest.raises(EngineInstallError, match="damaged"):
        _install_aether(tampered, engines_root)
    after = native_engines.installed("aether")
    assert after == before
    assert native_engines.verify_binary(after.root, after.manifest) is None
    assert not list((engines_root / "aether" / ".staging").iterdir())


def test_package_for_another_engine_or_platform_is_refused(tmp_path, engines_root):
    fake = tmp_path / "AETHER Engine.exe"
    fake.write_bytes(b"not really an engine")
    archive = tmp_path / "t.zip"
    native_engines.build_package("tethys", fake, archive, version="1", build_id="b", platform="windows-x64",
                                 extra={"protocol": 2})
    never = lambda *_: pytest.fail("must not run the binary")  # noqa: E731
    with pytest.raises(EngineInstallError, match="not a AETHER Engine package"):
        native_engines.install_package("aether", archive, compatible=lambda _m: None, probe=never,
                                       root=engines_root, platform="windows-x64")
    with pytest.raises(EngineInstallError, match="another computer type"):
        native_engines.install_package("tethys", archive, compatible=lambda _m: None, probe=never,
                                       root=engines_root, platform="linux-x64")
    assert native_engines.installed("aether", root=engines_root) is None


def test_update_keeps_one_previous_build_for_rollback(tmp_path, engines_root):
    ids = []
    for number in range(3):
        binary = tmp_path / f"{number}.exe"
        binary.write_bytes(f"engine {number}".encode())
        archive = tmp_path / f"{number}.zip"
        native_engines.build_package("tethys", binary, archive, version=f"1.{number}", build_id=f"b{number}",
                                     platform="windows-x64", extra={"protocol": 2})
        result = native_engines.install_package("tethys", archive, compatible=lambda _m: None,
                                                probe=lambda _b, _m: None, root=engines_root,
                                                platform="windows-x64", server_build_id=f"s{number}")
        ids.append(result.installed.install_id)
    folders = {child.name for child in (engines_root / "tethys").iterdir() if child.is_dir()} - {".staging"}
    assert folders == {ids[1], ids[2]}
    assert native_engines.installed("tethys", root=engines_root, platform="windows-x64").version == "1.2"


@windows_only
def test_tethys_locate_prefers_the_installed_engine(tmp_path, engines_root):
    from cloth_next.tethys import engine, protocol
    line = native_engines.run_version(TETHYS_EXE)
    version = line.split()[2]
    archive = tmp_path / "tethys.zip"
    native_engines.build_package("tethys", TETHYS_EXE, archive, version=version, build_id="1a96f14b650de870",
                                 platform="windows-x64", extra={"protocol": protocol.PROTOCOL_VERSION})
    native_engines.install_package("tethys", archive, compatible=engine.package_compatible,
                                   probe=engine.package_probe, platform="windows-x64")
    located = engine.locate()
    assert engines_root in located.parents
    assert engine.probe(located) == line


def test_choose_build_filters_engine_platform_backend_and_addon_range():
    builds = [
        {"engine": "gaia", "platform": "windows-x64", "backend": "cuda12.8", "build_id": "g", "version": "9",
         "published_at": "2026-11-09"},
        {"engine": "aether", "platform": "linux-x64", "backend": "cuda", "build_id": "l", "version": "1",
         "published_at": "2026-11-09"},
        {"engine": "aether", "platform": "windows-x64", "backend": "cuda", "build_id": "new", "version": "2",
         "published_at": "2026-11-08", "min_addon_version": "2.9.80"},
        {"engine": "aether", "platform": "windows-x64", "backend": "cuda", "build_id": "ok", "version": "1",
         "published_at": "2026-11-07", "min_addon_version": "2.9.60"},
    ]
    chosen = native_engines.choose_build(builds, "aether", "2.9.67", platform="windows-x64")
    assert chosen["build_id"] == "ok"
    assert native_engines.choose_build(builds, "aether", "2.9.80", platform="windows-x64")["build_id"] == "new"
    assert native_engines.choose_build(builds, "tethys", "2.9.80", platform="windows-x64") is None


def test_gaia_choice_ignores_other_engines_in_the_downloads_list():
    from cloth_next.polyworks.gaia_downloads import choose_build
    builds = [{"engine": "tethys", "platform": "windows-x64", "backend": "cuda12.8", "build_id": "t",
               "version": "9", "published_at": "2026-11-09"},
              {"engine": "gaia", "platform": "windows-x64", "backend": "cuda12.8", "build_id": "g",
               "version": "0.26", "published_at": "2026-10-22"}]
    assert choose_build(builds, "2.9.67")["build_id"] == "g"


# ----------------------------------------------------- against the contract double
@pytest.fixture
def backend(monkeypatch):
    pytest.importorskip("cryptography")
    from cloth_next.polyworks import gaia_downloads
    from tests.polyworks_backend import ContractBackend
    server = ContractBackend().start()
    monkeypatch.setenv(gaia_downloads.DOWNLOAD_HOST_ENV, "127.0.0.1")
    yield server
    server.stop()


def _service(backend, tmp_path, *, signed_in):
    from cloth_next.polyworks.api import Endpoints
    from cloth_next.polyworks.service import AccountService
    from cloth_next.polyworks.storage import LocalStore
    from cloth_next.polyworks.trust import load_trust
    from cloth_next.polyworks.vault import UserFileVault
    root = tmp_path / "account"
    clock = type("Clock", (), {"value": 1000.0, "__call__": lambda self: self.value})()
    service = AccountService(LocalStore(root), endpoints=Endpoints(backend.url, backend.url),
                             vault=UserFileVault(root), trust=load_trust({}, production=backend.trust_pems()),
                             monotonic=clock, client_name="Cloth NeXt test")
    if signed_in:
        user = backend.add_user("tethys@example.com")
        link = service.start_device_link()
        backend.approve(link.user_code, user)
        clock.value += link.interval
        assert service.poll_device_link(link)
        service.user = user
    return service


def test_public_aether_is_listed_and_downloaded_without_an_account(backend, tmp_path):
    from cloth_next.polyworks.gaia_downloads import download
    data = os.urandom(4096)
    backend.publish_engine_build("aether", data, version="0.4.6", channel="public", min_addon="2.9.67")
    backend.publish_engine_build("aether", b"dev", version="0.4.7-dev", channel="dev", min_addon="2.9.67",
                                 rev="bbbbbbbbbbbb", published_at="2026-11-08T00:00:00Z")
    service = _service(backend, tmp_path, signed_in=False)
    listed = service.engine_builds("aether", platform="windows-x64", backend="cuda", require_account=False)
    assert [build["version"] for build in listed] == ["0.4.6"]       # dev builds need a dev account
    chosen = native_engines.choose_build(listed, "aether", "2.9.67", platform="windows-x64")
    assert native_engines.choose_build(listed, "aether", "2.9.66", platform="windows-x64") is None
    target = download(lambda: service.engine_download_session("aether", chosen), tmp_path / "a.zip")
    assert target.read_bytes() == data
    assert not any(authenticated for _m, path, _r, authenticated in backend.requests)


def test_signed_in_dev_tester_also_sees_dev_aether_through_a_session(backend, tmp_path):
    from cloth_next.polyworks.gaia_downloads import download
    backend.publish_engine_build("aether", b"public", version="0.4.6", channel="public")
    backend.publish_engine_build("aether", b"dev build", version="0.4.7-dev", channel="dev",
                                 rev="bbbbbbbbbbbb", published_at="2026-11-08T00:00:00Z")
    service = _service(backend, tmp_path, signed_in=True)
    backend.gaia_testers.add(service.user)
    listed = service.engine_builds("aether", platform="windows-x64", backend="cuda", require_account=False)
    chosen = native_engines.choose_build(listed, "aether", "2.9.67", platform="windows-x64")
    assert chosen["version"] == "0.4.7-dev" and "url" not in chosen
    target = download(lambda: service.engine_download_session("aether", chosen), tmp_path / "d.zip")
    assert target.read_bytes() == b"dev build"


def test_tethys_needs_gaia_access(backend, tmp_path):
    from cloth_next.polyworks.gaia_downloads import download
    from cloth_next.polyworks.service import ServiceError
    backend.publish_engine_build("tethys", b"wet", version="1.3.0-dev", min_addon="2.9.67")
    anonymous = _service(backend, tmp_path / "a", signed_in=False)
    with pytest.raises(ServiceError) as exc:
        anonymous.engine_builds("tethys", platform="windows-x64", backend="cpu", require_account=True)
    assert exc.value.code == "sign_in_required"
    service = _service(backend, tmp_path / "b", signed_in=True)
    with pytest.raises(ServiceError) as exc:
        service.engine_builds("tethys", platform="windows-x64", backend="cpu", require_account=True)
    assert exc.value.code == "gaia_access_required"
    backend.gaia_access[service.user] = "granted"
    listed = service.engine_builds("tethys", platform="windows-x64", backend="cpu", require_account=True)
    assert [build["engine"] for build in listed] == ["tethys"]
    target = download(lambda: service.engine_download_session("tethys", listed[0]), tmp_path / "t.zip")
    assert target.read_bytes() == b"wet"


@windows_only
def test_preferences_install_downloads_verifies_and_never_selects(blender_env, backend, tmp_path, engines_root,
                                                                    monkeypatch):
    from cloth_next import manifest_version
    from cloth_next.blender import engine_installs
    archive = _aether_package(tmp_path)
    backend.publish_engine_build("aether", archive.read_bytes(), channel="public", min_addon="2.0.0")
    service = _service(backend, tmp_path, signed_in=False)
    monkeypatch.setattr(engine_installs, "_service", lambda: service)
    monkeypatch.setattr(engine_installs, "_bake_active", lambda: False)
    solver_root = tmp_path / "solver"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert engine_installs.install("aether")
    engine_installs._state["worker"].join(timeout=60)
    state = engine_installs.snapshot("aether")
    assert state.error == "" and "is installed" in state.message, state
    installed = native_engines.installed("aether")
    assert installed is not None and installed.server_build_id.startswith("aether-")
    assert engine_installs.update_available("aether") is None
    # The solver registry was never written: the selected engine cannot have changed.
    assert not (tmp_path / "local" / "ClothNeXt" / "solver" / "registry.json").exists()
    assert not solver_root.exists()
    assert not list((engines_root / ".downloads").iterdir())
    assert manifest_version()
