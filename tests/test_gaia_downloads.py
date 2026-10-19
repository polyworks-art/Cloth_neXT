# SPDX-License-Identifier: GPL-3.0-or-later
"""GAIA access and verified, resumable downloads against the contract test double."""
from __future__ import annotations

import hashlib
import logging
import os
import time

import pytest

pytest.importorskip("cryptography")

from cloth_next.polyworks import gaia_downloads
from cloth_next.polyworks.api import Endpoints
from cloth_next.polyworks.gaia_downloads import DownloadError, choose_build, download
from cloth_next.polyworks.service import AccountService, ServiceError
from cloth_next.polyworks.storage import LocalStore
from cloth_next.polyworks.trust import load_trust
from cloth_next.polyworks.vault import UserFileVault
from tests.polyworks_backend import ContractBackend

PAYLOAD = os.urandom(3 * 1024 * 1024 + 123)


class Clock:
    value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture
def backend(monkeypatch):
    server = ContractBackend().start()
    monkeypatch.setenv(gaia_downloads.DOWNLOAD_HOST_ENV, "127.0.0.1")
    yield server
    server.stop()


@pytest.fixture
def service(backend, tmp_path):
    root = tmp_path / "account"
    clock = Clock()
    svc = AccountService(LocalStore(root), endpoints=Endpoints(backend.url, backend.url),
                         vault=UserFileVault(root), trust=load_trust({}, production=backend.trust_pems()),
                         monotonic=clock, client_name="Cloth NeXt test / Blender")
    user = backend.add_user("gaia@example.com")
    link = svc.start_device_link()
    backend.approve(link.user_code, user)
    clock.value += link.interval
    assert svc.poll_device_link(link)
    svc.user = user
    return svc


def granted(backend, service):
    backend.gaia_access[service.user] = "granted"
    return backend.publish_gaia_build(PAYLOAD)


def test_access_states_request_and_invite(backend, service):
    assert service.gaia_access() == {"status": None, "mode": "selected"}
    assert service.request_gaia_access() == "pending"
    assert service.gaia_access()["status"] == "pending"
    backend.gaia_invites["invite-token"] = 1
    assert service.redeem_gaia_invite("https://account.cloth-next.de/gaia-invite#invite-token")
    assert service.gaia_access()["status"] == "granted"
    with pytest.raises(ServiceError) as caught:
        backend.gaia_access.pop(service.user)
        service.redeem_gaia_invite("invite-token")  # used up
    assert caught.value.code == "invalid_code"
    backend.gaia_mode = "none"
    with pytest.raises(ServiceError) as caught:
        service.request_gaia_access()
    assert caught.value.code == "requests_closed"
    backend.gaia_mode = "surprise"
    assert service.gaia_access()["mode"] == "none"  # unknown values mean no access


def test_denied_access_lists_nothing_and_cannot_open_a_session(backend, service):
    build_id = backend.publish_gaia_build(PAYLOAD)
    listing = service.gaia_downloads()
    assert listing == {"enabled": True, "access": False, "builds": []}
    with pytest.raises(ServiceError) as caught:
        service.gaia_download_session(build_id)
    assert caught.value.code == "gaia_access_required"


def test_build_choice_respects_platform_backend_channel_and_addon_range(backend, service):
    granted(backend, service)
    backend.publish_gaia_build(b"x", version="0.27.0", min_addon="9.0.0", rev="aaaaaaaaaaaa")
    backend.publish_gaia_build(b"y", version="0.28.0", backend="cpu", rev="bbbbbbbbbbbb")
    builds = service.gaia_downloads()["builds"]
    chosen = choose_build(builds, "2.9.25")
    assert chosen["version"] == "0.26.0" and chosen["backend"] == "cuda12.8"
    assert choose_build(builds, "1.0.0") is None


@pytest.mark.parametrize("channel, label", [("dev", "Dev release"), ("public", "Public release")])
def test_server_channel_is_a_label_not_a_filter(channel, label):
    from cloth_next.polyworks.gaia_downloads import channel_label
    build = {"build_id": "gaia-0.26.0-dev.20261019+cuda12.8.windows-x64.bf3dad56f7a0",
             "version": "0.26.0-dev.20261019", "sha256": "0" * 64, "size_bytes": 1,
             "channel": channel, "platform": "windows-x64", "backend": "cuda12.8",
             "min_addon_version": "2.9.29", "max_addon_version": None, "published_at": None}
    assert choose_build([build], "2.9.30") is build
    assert channel_label(build) == label


def test_download_verifies_and_never_logs_the_signed_url(backend, service, tmp_path, caplog):
    build_id = granted(backend, service)
    caplog.set_level(logging.DEBUG)
    seen = []
    target = download(lambda: service.gaia_download_session(build_id), tmp_path / "gaia.zip",
                      progress=lambda done, total: seen.append((done, total)))
    assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(PAYLOAD).digest()
    assert seen[-1] == (len(PAYLOAD), len(PAYLOAD))
    assert not (tmp_path / "gaia.zip.partial").exists()
    assert "?t=" not in caplog.text and "t=" not in "".join(r.getMessage() for r in caplog.records)


def test_dropped_connection_resumes_with_range(backend, service, tmp_path):
    build_id = granted(backend, service)
    backend.cut_download_after = 1024 * 1024 + 7
    sleeps = []
    target = download(lambda: service.gaia_download_session(build_id), tmp_path / "gaia.zip",
                      sleep=sleeps.append)
    assert target.read_bytes() == PAYLOAD and sleeps == [2]
    ranges = [r for r in backend.requests if r[1].startswith("/gaia/")]
    assert len(ranges) == 2


def test_server_without_range_support_restarts_from_zero(backend, service, tmp_path):
    build_id = granted(backend, service)
    backend.cut_download_after = 500_000
    backend.no_range = True
    target = download(lambda: service.gaia_download_session(build_id), tmp_path / "gaia.zip",
                      sleep=lambda _s: None)
    assert target.read_bytes() == PAYLOAD


def test_expired_link_gets_a_fresh_session_and_resumes(backend, service, tmp_path):
    build_id = granted(backend, service)
    calls = []

    def session():
        calls.append(1)
        value = service.gaia_download_session(build_id)
        if len(calls) == 1:
            backend.clock = lambda: time.time() + 7 * 3600  # first link is now expired
        return value
    assert download(session, tmp_path / "gaia.zip").read_bytes() == PAYLOAD
    assert len(calls) == 2


def test_revoked_access_stops_and_deletes_the_partial(backend, service, tmp_path):
    build_id = granted(backend, service)
    backend.cut_download_after = 100_000

    def sleep(_seconds):
        backend.gaia_access.pop(service.user)  # revoked while offline
    with pytest.raises(DownloadError) as caught:
        download(lambda: service.gaia_download_session(build_id), tmp_path / "gaia.zip", sleep=sleep)
    assert caught.value.code == "gaia_access_required"
    assert not (tmp_path / "gaia.zip.partial").exists() and not (tmp_path / "gaia.zip").exists()


def test_checksum_mismatch_deletes_the_file(backend, service, tmp_path):
    build_id = granted(backend, service)
    session = service.gaia_download_session(build_id)
    backend.build_files[build_id] = bytes(len(PAYLOAD))  # same size, wrong content
    with pytest.raises(DownloadError) as caught:
        download(lambda: session, tmp_path / "gaia.zip")
    assert caught.value.code == "checksum_mismatch"
    assert not list(tmp_path.glob("gaia.zip*"))


def test_withdrawn_build_and_unknown_hosts_are_refused(backend, service, tmp_path, monkeypatch):
    build_id = granted(backend, service)
    session = service.gaia_download_session(build_id)
    del backend.build_files[build_id]
    with pytest.raises(DownloadError) as caught:
        download(lambda: session, tmp_path / "gaia.zip")
    assert caught.value.code == "build_unavailable"
    monkeypatch.delenv(gaia_downloads.DOWNLOAD_HOST_ENV)
    for url in ("http://127.0.0.1/x", "https://evil.example/gaia/x", "ftp://downloads.cloth-next.de/x",
                "https://user:pw@downloads.cloth-next.de/x"):
        with pytest.raises(DownloadError):
            gaia_downloads.validate_download_url(url)
    gaia_downloads.validate_download_url("https://downloads.cloth-next.de/gaia/b/f.zip?t=abc")


def test_backend_unavailable_is_reported_as_offline(backend, service):
    backend.edge_503 = True
    with pytest.raises(ServiceError) as caught:
        service.gaia_access()
    assert caught.value.code == "service_unavailable"
