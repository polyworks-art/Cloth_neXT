# SPDX-License-Identifier: GPL-3.0-or-later
"""Companion API client and account workflows against the isolated contract backend."""
from __future__ import annotations

import json
import logging
import os

import pytest

pytest.importorskip("cryptography")

from cloth_next.polyworks import messages
from cloth_next.polyworks.api import ApiError, Endpoints, PolyWorksApi, validate_origin
from cloth_next.polyworks.entitlements import EntitlementError, verify
from cloth_next.polyworks.service import AccountService, ServiceError
from cloth_next.polyworks.status import LicenseStatus
from cloth_next.polyworks.storage import LocalStore
from cloth_next.polyworks.vault import UserFileVault
from cloth_next.polyworks.trust import load_trust
from tests.polyworks_backend import ContractBackend


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture
def backend():
    server = ContractBackend().start()
    yield server
    server.stop()


@pytest.fixture
def make_service(backend, tmp_path):
    def build(root=None, **kwargs):
        root = root or tmp_path / "account"
        store = LocalStore(root)
        monotonic = kwargs.pop("monotonic", Clock())
        service = AccountService(
            store, endpoints=Endpoints(backend.url, backend.url), vault=UserFileVault(root),
            trust=load_trust({}, production=backend.trust_pems()), monotonic=monotonic,
            client_name="Cloth NeXt test / Companion", **kwargs)
        service.test_monotonic = monotonic
        return service
    return build


def sign_in(service, backend, user_id):
    link = service.start_device_link()
    backend.approve(link.user_code, user_id)
    service.test_monotonic.value += link.interval
    assert service.poll_device_link(link) is True
    return link


def stored_text(service):
    root = service.store.root
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in root.iterdir() if p.is_file() and p.name != "session.json")


# Authentication ------------------------------------------------------------------
def test_device_link_success_signs_in_and_loads_licences(backend, make_service):
    user = backend.add_user("artist@example.com")
    backend.add_license(user)
    service = make_service()
    link = service.start_device_link()
    assert link.user_code.startswith("BCDF-")
    assert link.verification_uri_complete.startswith(backend.url + "/link?code=")
    assert "device_code" not in repr(link)
    assert service.poll_device_link(link) is False  # interval not yet elapsed: no request
    service.test_monotonic.value += link.interval
    assert service.poll_device_link(link) is False  # authorization_pending
    backend.approve(link.user_code, user)
    service.test_monotonic.value += link.interval
    assert service.poll_device_link(link) is True
    state = service.state()
    assert state["account"]["signed_in"] and state["account"]["email"] == "artist@example.com"
    assert len(state["licenses"]) == 1 and state["licenses"][0]["usable"]
    assert "future_field" not in json.dumps(state)
    view = service.view()
    assert view.status == LicenseStatus.ACTIVATION_REQUIRED and not view.authorized
    start_body = json.loads(backend.requests[0][2])
    assert start_body == {"client_name": "Cloth NeXt test / Companion"}


def test_device_link_rejection_and_expiry_and_single_use(backend, make_service):
    service = make_service()
    link = service.start_device_link()
    backend.deny(link.user_code)
    service.test_monotonic.value += link.interval
    with pytest.raises(ServiceError) as denied:
        service.poll_device_link(link)
    assert denied.value.code == "access_denied"
    link = service.start_device_link()
    service.test_monotonic.value = link.expires_at + 1
    with pytest.raises(ServiceError) as expired:
        service.poll_device_link(link)
    assert expired.value.code == "expired_token"
    user = backend.add_user("a@example.com")
    link = sign_in(service, backend, user)
    service.test_monotonic.value += link.interval
    with pytest.raises(ServiceError) as reused:
        service.poll_device_link(link)
    assert reused.value.code == "invalid_device_code"


def test_slow_down_adds_five_seconds(backend, make_service, monkeypatch):
    service = make_service()
    link = service.start_device_link()
    monkeypatch.setattr(service.api, "device_token",
                        lambda _code: (_ for _ in ()).throw(ApiError("slow_down", 400)))
    service.test_monotonic.value += link.interval
    before = link.interval
    assert service.poll_device_link(link) is False
    assert link.interval == before + 5


def test_password_login_mfa_and_invalid_credentials(backend, make_service):
    backend.add_user("pw@example.com", "secret-pass", mfa="123456")
    service = make_service()
    with pytest.raises(ServiceError) as wrong:
        service.login_password("pw@example.com", "nope")
    assert wrong.value.code == "invalid_credentials"
    challenge = service.login_password("pw@example.com", "secret-pass")
    assert challenge is not None and "mfa_token" not in repr(challenge)
    with pytest.raises(ServiceError) as bad_code:
        service.login_mfa(challenge, "000000")
    assert bad_code.value.code == "mfa_invalid" and challenge.attempts_left == 4
    service.login_mfa(challenge, "123 456")
    assert service.state()["account"]["signed_in"]
    assert "secret-pass" not in stored_text(service)


def test_tokens_never_reach_public_files_or_logs(backend, make_service, caplog):
    caplog.set_level(logging.DEBUG)
    user = backend.add_user("t@example.com")
    license_id = backend.add_license(user)
    service = make_service()
    sign_in(service, backend, user)
    service.activate(license_id, "Workstation")
    tokens = [token for token in backend.sessions]
    public = stored_text(service)
    assert tokens and all(token not in public for token in tokens)
    assert all(token not in caplog.text for token in tokens)
    if os.name != "nt":  # Windows relies on the per-user ACL of %LOCALAPPDATA%
        session_file = service.store.root / "session.json"
        assert oct(session_file.stat().st_mode & 0o777) == "0o600"


# Licences and devices --------------------------------------------------------------
def test_activation_verifies_and_stores_entitlement_idempotently(backend, make_service):
    user = backend.add_user("act@example.com")
    license_id = backend.add_license(user, tier="personal")
    service = make_service()
    sign_in(service, backend, user)
    view = service.activate(license_id, "  Studio   PC  ")
    assert view.status == LicenseStatus.ACTIVE and view.authorized
    assert view.tier == "Personal" and view.device_label == "Studio PC"
    token = service.store.read_entitlement()
    verify(token, trust=service.trust, installation_id=service.store.read_installation_id())
    service.activate(license_id, "Studio PC")  # same installation: no new slot
    assert len([a for a in backend.activations.values() if a["active"]]) == 1
    assert service.state()["licenses"][0]["devices_active"] == 1
    installation = service.store.read_installation_id()
    assert installation.startswith("inst_") and len(installation) == 37


def test_device_limit_lists_devices_then_deactivate_and_retry(backend, make_service, tmp_path):
    user = backend.add_user("limit@example.com")
    license_id = backend.add_license(user)
    for index in range(3):
        other = make_service(tmp_path / f"other{index}")
        sign_in(other, backend, user)
        other.activate(license_id, f"Machine {index}")
    service = make_service()
    sign_in(service, backend, user)
    with pytest.raises(ServiceError) as limit:
        service.activate(license_id, "Laptop")
    assert limit.value.code == "device_limit"
    assert str(limit.value) == messages.DEVICE_LIMIT
    assert service.view().message == messages.DEVICE_LIMIT_BLENDER
    devices = service.devices(license_id)
    assert len(devices) == 3
    service.deactivate(license_id, devices[0]["id"])
    assert service.activate(license_id, "Laptop").authorized


def test_deactivating_this_computer_removes_entitlement(backend, make_service):
    user = backend.add_user("self@example.com")
    license_id = backend.add_license(user)
    service = make_service()
    sign_in(service, backend, user)
    service.activate(license_id, "Here")
    mine = service.state()["activation"]["activation_id"]
    view = service.deactivate(license_id, mine)
    assert service.store.read_entitlement() is None
    assert view.status == LicenseStatus.ACTIVATION_REQUIRED


def test_multiple_licences_and_account_without_licence(backend, make_service, tmp_path):
    user = backend.add_user("multi@example.com")
    backend.add_license(user, tier="personal")
    backend.add_license(user, tier="commercial", seats=5)
    service = make_service()
    sign_in(service, backend, user)
    assert {item["tier"] for item in service.state()["licenses"]} == {"personal", "commercial"}
    empty_user = backend.add_user("none@example.com")
    empty = make_service(tmp_path / "empty")
    sign_in(empty, backend, empty_user)
    assert empty.state()["licenses"] == []
    assert empty.view().status == LicenseStatus.NO_LICENSE


def test_inactive_licence_cannot_be_activated(backend, make_service):
    user = backend.add_user("susp@example.com")
    license_id = backend.add_license(user, state="suspended")
    service = make_service()
    sign_in(service, backend, user)
    assert service.view().status == LicenseStatus.SUSPENDED
    with pytest.raises(ServiceError) as inactive:
        service.activate(license_id, "x")
    assert inactive.value.code == "license_inactive"
    assert service.store.read_entitlement() is None


def test_redeem_code_and_unverified_email(backend, make_service, tmp_path):
    user = backend.add_user("redeem@example.com")
    code = backend.add_redeem_code(tier="personal")
    service = make_service()
    sign_in(service, backend, user)
    with pytest.raises(ServiceError) as unknown:
        service.redeem("WRONG")
    assert unknown.value.code == "not_found"
    view = service.redeem(code)
    assert view.status == LicenseStatus.ACTIVATION_REQUIRED
    unverified = backend.add_user("unverified@example.com", verified=False)
    other = make_service(tmp_path / "other")
    sign_in(other, backend, unverified)
    with pytest.raises(ServiceError) as forbidden:
        other.redeem(backend.add_redeem_code())
    assert forbidden.value.code == "forbidden"


# Check-in, revocation and offline -----------------------------------------------------
@pytest.mark.parametrize("change, status, reason", [
    (dict(state="revoked"), LicenseStatus.REVOKED, "license_revoked"),
    (dict(state="suspended"), LicenseStatus.SUSPENDED, "license_suspended"),
    (dict(purchase_status="refunded"), LicenseStatus.REVOKED, "refunded"),
])
def test_check_in_revocation_deletes_entitlement(backend, make_service, change, status, reason):
    user = backend.add_user("rev@example.com")
    license_id = backend.add_license(user)
    service = make_service()
    sign_in(service, backend, user)
    service.activate(license_id, "Here")
    backend.set_license_state(license_id, **change)
    view = service.refresh()
    assert service.store.read_entitlement() is None
    assert view.status == status and not view.authorized
    assert view.message == messages.revocation_message(reason)


def test_device_deactivated_elsewhere_reports_reactivation(backend, make_service):
    user = backend.add_user("dev@example.com")
    license_id = backend.add_license(user)
    service = make_service()
    sign_in(service, backend, user)
    service.activate(license_id, "Here")
    for activation in backend.activations.values():
        activation["active"] = False  # deactivated in the portal
    view = service.refresh()
    assert view.status == LicenseStatus.DEVICE_DEACTIVATED
    assert service.activate(license_id, "Here").authorized


def test_offline_keeps_entitlement_and_backs_off_then_recovers(backend, make_service):
    clock = Clock()
    clock.value = float(int(__import__("time").time()))
    user = backend.add_user("off@example.com")
    license_id = backend.add_license(user)
    backend.clock = clock
    service = make_service(clock=clock)
    sign_in(service, backend, user)
    service.activate(license_id, "Here")
    backend.offline = True
    for expected in (60, 300, 900, 3600, 3600):
        with pytest.raises(ServiceError) as offline:
            service.refresh()
        assert offline.value.code == "server_error"
        connection = service.state()["connection"]
        assert connection["online"] is False
        from cloth_next.polyworks.state import parse_iso
        assert round(parse_iso(connection["next_check_at"]) - clock.value) == expected
    view = service.view()
    assert view.status == LicenseStatus.OFFLINE_VERIFIED and view.authorized
    assert view.message == messages.OFFLINE
    backend.edge_503 = True
    backend.offline = False
    with pytest.raises(ServiceError) as edge:
        service.refresh()
    assert edge.value.code == "service_unavailable" and service.view().authorized
    backend.edge_503 = False
    assert service.refresh().status == LicenseStatus.ACTIVE
    assert service.state()["connection"]["retry_step"] == 0


def test_network_error_when_server_is_gone(backend, make_service):
    user = backend.add_user("gone@example.com")
    service = make_service()
    sign_in(service, backend, user)
    backend.stop()
    with pytest.raises(ServiceError) as gone:
        service.refresh()
    assert gone.value.code == "network_unavailable"
    assert service.view().account_label == "Signed in"


def test_session_expiry_keeps_offline_entitlement(backend, make_service):
    user = backend.add_user("exp@example.com")
    license_id = backend.add_license(user)
    service = make_service()
    sign_in(service, backend, user)
    service.activate(license_id, "Here")
    backend.end_sessions(user)  # portal sign-out, password reset or account deletion
    with pytest.raises(ServiceError) as dead:
        service.refresh()
    assert dead.value.code == "not_authenticated"
    assert str(dead.value) == messages.SESSION_EXPIRED
    view = service.view()
    assert view.account_label == "Session expired" and view.authorized
    assert any("session has expired" in w for w in view.warnings)
    assert service.store.read_entitlement() is not None
    assert not (service.store.root / "session.json").exists()


def test_tampered_or_foreign_entitlement_is_never_stored(backend, make_service, monkeypatch):
    user = backend.add_user("tamper@example.com")
    license_id = backend.add_license(user)
    service = make_service()
    sign_in(service, backend, user)
    real = service.api.activate

    def forged(*args):
        activation, token = real(*args)
        payload, signature = token.split(".")
        return activation, payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB") + "." + signature
    monkeypatch.setattr(service.api, "activate", forged)
    with pytest.raises(ServiceError) as untrusted:
        service.activate(license_id, "x")
    assert untrusted.value.code == "untrusted_entitlement"
    assert service.store.read_entitlement() is None
    other = ContractBackend()  # entitlement signed by a key that is not pinned
    token = other.sign({"typ": "polyworks.entitlement", "v": 1, "kid": "test-key-1"})
    with pytest.raises(EntitlementError) as foreign:
        verify(token, trust=service.trust, installation_id="inst_x")
    assert foreign.value.code == "bad_signature"


def test_check_in_invalid_and_unknown_status(backend, make_service, monkeypatch):
    user = backend.add_user("inv@example.com")
    license_id = backend.add_license(user)
    service = make_service()
    sign_in(service, backend, user)
    service.activate(license_id, "Here")
    monkeypatch.setattr(service.api.transport, "request", lambda *a: (
        200, b'{"status":"surprise","reason":"new_reason"}', None)
        if a[1] == "/v1/entitlements/check-in" else original(*a))
    original = type(service.api.transport).request.__get__(service.api.transport)
    view = service.refresh()
    assert view.status == LicenseStatus.REVOKED  # unknown status is never valid
    assert service.store.read_entitlement() is None


def test_rate_limit_and_malformed_responses(backend, make_service):
    user = backend.add_user("rl@example.com")
    service = make_service()
    sign_in(service, backend, user)
    backend.rate_limit_next = 1
    with pytest.raises(ServiceError) as limited:
        service.refresh()
    assert limited.value.code == "rate_limited" and limited.value.retry_after == 7
    assert service.state()["connection"]["last_error"] == "rate_limited"
    backend.malformed_next = 1
    with pytest.raises(ServiceError) as malformed:
        service.refresh()
    assert malformed.value.code == "invalid_response"
    assert service.state()["account"]["signed_in"]


def test_sign_out_keeps_or_releases_device(backend, make_service, tmp_path):
    user = backend.add_user("out@example.com")
    license_id = backend.add_license(user)
    keep = make_service()
    sign_in(keep, backend, user)
    keep.activate(license_id, "Keep")
    message = keep.sign_out()
    assert "stays activated" in message
    assert keep.view().account_label == "Not signed in" and keep.view().authorized
    assert all(s["ended"] for s in backend.sessions.values())
    release = make_service(tmp_path / "release")
    sign_in(release, backend, user)
    release.activate(license_id, "Release")
    assert "deactivated" in release.sign_out(release_device=True)
    assert release.store.read_entitlement() is None
    assert release.view().status == LicenseStatus.NOT_SIGNED_IN
    assert sum(a["active"] for a in backend.activations.values()) == 1


def test_sign_out_when_offline_still_clears_local_session(backend, make_service):
    user = backend.add_user("offout@example.com")
    service = make_service()
    sign_in(service, backend, user)
    backend.offline = True
    assert "could not be reached" in service.sign_out()
    assert service._token() is None


def test_sync_if_due_respects_schedule(backend, make_service):
    user = backend.add_user("due@example.com")
    service = make_service()
    sign_in(service, backend, user)
    count = len(backend.requests)
    assert service.sync_if_due() is None and len(backend.requests) == count
    assert service.sync_if_due(force=True) is not None


def test_concurrent_operation_is_rejected(make_service, tmp_path):
    first, second = make_service(), make_service()
    with first.operation():
        with pytest.raises(ServiceError) as busy:
            with second.operation():
                pass
    assert busy.value.code == "busy"


def test_endpoint_validation():
    assert validate_origin("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert Endpoints.from_environment({}).api == "https://api.cloth-next.de"
    assert validate_origin("http://[::1]:8000") == "http://[::1]:8000"
    for bad in ("http://api.cloth-next.de", "https://user:pw@x.de", "https://x.de/path", "ftp://x",
                "http://localhost:8000"):
        with pytest.raises(ValueError):
            validate_origin(bad)
    endpoints = Endpoints("https://api.cloth-next.de", "https://account.cloth-next.de")
    assert endpoints.browser_url("https://account.cloth-next.de/link?code=A")
    with pytest.raises(ApiError):
        endpoints.browser_url("https://evil.example/link")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_user_file_vault_discards_session_readable_by_others(tmp_path):
    vault = UserFileVault(tmp_path)
    vault.save({"token": "secret"})
    assert vault.load() == {"token": "secret"}
    os.chmod(vault.path, 0o644)
    assert vault.load() is None
    assert not vault.path.exists()


def test_api_sends_no_extra_fields(backend):
    api = PolyWorksApi(Endpoints(backend.url, backend.url))
    backend.add_user("strict@example.com", "pw")
    session = api.login("strict@example.com", "pw")
    assert "token" not in repr(session).lower().replace("session(", "")
    assert api.me(session.token)["email"] == "strict@example.com"
    assert api.licenses(session.token) == []
    assert api.public_key()["key_id"] == "test-key-1"
