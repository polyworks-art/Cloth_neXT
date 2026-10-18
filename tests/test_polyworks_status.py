# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline entitlement verification, local storage and the shared status machine."""
from __future__ import annotations

import json
import os

import pytest

from cloth_next.polyworks import ed25519
from cloth_next.polyworks.entitlements import EntitlementError, verify
from cloth_next.polyworks.state import sanitize, utc_now_iso
from cloth_next.polyworks.status import LicenseStatus, compute_view, read_view
from cloth_next.polyworks.storage import LocalStore, StorageError, account_root
from cloth_next.polyworks.trust import TrustStore, load_trust

RFC8032 = [  # (public key, message, signature) from RFC 8032 section 7.1
    ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
     "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
     "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
]
INSTALLATION = "inst_" + "a" * 32
NOW = 1_800_000_000


@pytest.mark.parametrize("public, message, signature", RFC8032)
def test_reference_verifier_matches_rfc8032(public, message, signature):
    public, message, signature = (bytes.fromhex(v) for v in (public, message, signature))
    assert ed25519.verify(public, message, signature, use_provider=False)
    assert not ed25519.verify(public, message + b"x", signature, use_provider=False)
    broken = bytearray(signature)
    broken[10] ^= 1
    assert not ed25519.verify(public, message, bytes(broken), use_provider=False)


def test_pem_parsing_rejects_other_keys():
    with pytest.raises(ed25519.KeyFormatError):
        ed25519.raw_public_key_from_pem("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----")
    with pytest.raises(ed25519.KeyFormatError):
        ed25519.raw_public_key_from_pem("not pem")


@pytest.fixture
def signer():
    pytest.importorskip("cryptography")
    from tests.polyworks_backend import ContractBackend
    backend = ContractBackend(key_id="kid-a")
    trust = load_trust({}, production=backend.trust_pems())

    def make(**overrides):
        payload = {"typ": "polyworks.entitlement", "v": 1, "kid": "kid-a", "lic": "lic-1",
                   "sub": "user-1", "act": "act-1", "inst": INSTALLATION, "prd": "cloth-next",
                   "tier": "commercial", "seats": 3, "seat": "seat-1",
                   "iat": NOW - 10, "exp": NOW + 30 * 86400}
        payload.update(overrides)
        return backend.sign(payload)
    make.trust = trust
    make.backend = backend
    return make


@pytest.mark.parametrize("use_provider", [True, False])
def test_valid_entitlement_with_both_verifiers(signer, monkeypatch, use_provider):
    if not use_provider:
        monkeypatch.setattr(ed25519, "_verify_cryptography", lambda *a: None)
    entitlement = verify(signer(), trust=signer.trust, installation_id=INSTALLATION, now=NOW)
    assert entitlement.tier == "commercial" and entitlement.seats == 3
    assert entitlement.license_id == "lic-1" and not entitlement.development_key


@pytest.mark.parametrize("overrides, now, code", [
    (dict(v=2), NOW, "unsupported_version"),
    (dict(typ="other"), NOW, "malformed"),
    (dict(inst="inst_" + "b" * 32), NOW, "wrong_installation"),
    (dict(prd="other-product"), NOW, "wrong_product"),
    (dict(kid="unknown"), NOW, "unknown_key"),
    (dict(iat=NOW + 301), NOW, "not_yet_valid"),
    (dict(exp=NOW - 301), NOW, "expired"),
    (dict(iat="soon"), NOW, "malformed"),
])
def test_entitlement_rejections(signer, overrides, now, code):
    with pytest.raises(EntitlementError) as error:
        verify(signer(**overrides), trust=signer.trust, installation_id=INSTALLATION, now=now)
    assert error.value.code == code


def test_clock_skew_window_is_exactly_300_seconds(signer):
    verify(signer(iat=NOW + 300), trust=signer.trust, installation_id=INSTALLATION, now=NOW)
    verify(signer(exp=NOW - 300), trust=signer.trust, installation_id=INSTALLATION, now=NOW)


def test_tampering_and_format_errors(signer):
    token = signer()
    payload, signature = token.split(".")
    other = signer(tier="personal").split(".")[0]
    for bad, code in ((other + "." + signature, "bad_signature"), (payload, "malformed"),
                      (payload + "=." + signature, "malformed"), ("", "malformed"),
                      (token + ".extra", "malformed"), ("x" * 20000, "malformed")):
        with pytest.raises(EntitlementError) as error:
            verify(bad, trust=signer.trust, installation_id=INSTALLATION, now=NOW)
        assert error.value.code == code


def test_development_trust_file_is_flagged_and_cannot_shadow_pinned(signer, tmp_path):
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"keys": [{"key_id": "kid-a", "public_key_pem": "bogus"},
                                         {"key_id": "kid-dev",
                                          "public_key_pem": signer.backend.public_key_pem}]}))
    environ = {"CLOTH_NEXT_POLYWORKS_TRUST_FILE": str(path)}
    assert load_trust(environ, production={}).keys == {}  # one bad entry rejects the file
    path.write_text(json.dumps({"keys": [{"key_id": "kid-dev",
                                          "public_key_pem": signer.backend.public_key_pem}]}))
    trust = load_trust(environ, production={})
    entitlement = verify(signer(kid="kid-dev"), trust=trust, installation_id=INSTALLATION, now=NOW)
    assert entitlement.development_key
    pinned = load_trust(environ, production=signer.backend.trust_pems())
    assert not pinned.is_development("kid-a")


PRODUCTION_K1 = bytes.fromhex("fd71a1720dc4d89fdc82c175f12ca82b769748a730cb558cf61060a5d5ab8294")


def test_production_key_is_pinned(signer, tmp_path):
    trust = load_trust({})
    assert trust.keys == {"k1": PRODUCTION_K1} and not trust.is_development("k1")
    # A development trust file can never replace the pinned production kid.
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"keys": [{"key_id": "k1",
                                          "public_key_pem": signer.backend.public_key_pem}]}))
    shadow = load_trust({"CLOTH_NEXT_POLYWORKS_TRUST_FILE": str(path)})
    assert shadow.key("k1") == PRODUCTION_K1 and not shadow.is_development("k1")
    with pytest.raises(EntitlementError):
        verify(signer(kid="k1"), trust=shadow, installation_id=INSTALLATION, now=NOW)


def test_installation_id_is_created_once_and_never_regenerated(tmp_path):
    store = LocalStore(tmp_path / "root")
    first = store.installation_id()
    assert first == store.installation_id() == LocalStore(tmp_path / "root").installation_id()
    assert first.startswith("inst_") and len(first) == 37
    (tmp_path / "root" / "installation-id").write_text("damaged!")
    with pytest.raises(StorageError):
        store.installation_id()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission model")
def test_untrusted_files_are_ignored(tmp_path, signer):
    store = LocalStore(tmp_path)
    store.installation_id()
    store.write_entitlement(signer())
    assert oct((tmp_path / "entitlement").stat().st_mode & 0o777) == "0o600"
    assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"
    os.chmod(tmp_path / "entitlement", 0o666)
    assert store.read_entitlement() is None
    os.chmod(tmp_path / "entitlement", 0o600)
    (tmp_path / "link").symlink_to(tmp_path / "entitlement")
    assert store.read_json("link") is None


def test_account_root_is_per_user_not_per_blender_version(tmp_path):
    assert account_root({"CLOTH_NEXT_ACCOUNT_ROOT": str(tmp_path)}) == tmp_path
    assert "blender" not in str(account_root({})).lower()


def test_state_sanitizer_drops_unknown_and_secret_fields():
    clean = sanitize({"token": "secret", "account": {"signed_in": True, "token": "secret",
                                                     "email": "a@b.c\x07"},
                      "licenses": [{"id": "l", "usable": True, "entitlement": "secret"}]})
    text = json.dumps(clean)
    assert "secret" not in text and clean["account"]["email"] == "a@b.c"


def state(**sections):
    base = {"account": {"signed_in": True, "session": "active", "email": "a@example.com"},
            "licenses": [], "connection": {"online": True}, "check_in": {}}
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


LICENSE = {"id": "lic-1", "tier": "commercial", "state": "active", "usable": True,
           "seats": 3, "devices_active": 1, "device_limit": 3}


@pytest.mark.parametrize("published, token_kwargs, expected", [
    (None, None, LicenseStatus.NOT_SIGNED_IN),
    (state(account={"signed_in": False, "session": "none"}), None, LicenseStatus.NOT_SIGNED_IN),
    (state(account={"signed_in": False, "session": "expired"}), None, LicenseStatus.SESSION_EXPIRED),
    (state(licenses=None), None, LicenseStatus.UNKNOWN),
    (state(), None, LicenseStatus.NO_LICENSE),
    (state(licenses=[LICENSE]), None, LicenseStatus.ACTIVATION_REQUIRED),
    (state(licenses=[{**LICENSE, "usable": False, "state": "suspended"}]), None, LicenseStatus.SUSPENDED),
    (state(licenses=[{**LICENSE, "usable": False, "state": "revoked"}]), None, LicenseStatus.REVOKED),
    (state(check_in={"status": "revoked", "reason": "device_deactivated"}), None,
     LicenseStatus.DEVICE_DEACTIVATED),
    (state(check_in={"status": "revoked", "reason": "brand_new_reason"}), None, LicenseStatus.REVOKED),
    (state(check_in={"status": "invalid"}), None, LicenseStatus.ACTIVATION_REQUIRED),
    (state(licenses=[LICENSE], check_in={"status": "valid"}), {}, LicenseStatus.ACTIVE),
    (state(licenses=[LICENSE], connection={"online": False}), {}, LicenseStatus.OFFLINE_VERIFIED),
    (None, {}, LicenseStatus.OFFLINE_VERIFIED),  # Companion never ran on this profile
    (state(), {"exp": NOW - 400}, LicenseStatus.EXPIRED),
    (state(), {"iat": NOW + 4000}, LicenseStatus.INVALID),
    (state(), {"inst": "inst_" + "z" * 32}, LicenseStatus.INVALID),
    (state(), {"kid": "unknown"}, LicenseStatus.INVALID),
])
def test_status_state_machine(signer, published, token_kwargs, expected):
    token = signer(**token_kwargs) if token_kwargs is not None else None
    view = compute_view(state=published, token=token, installation_id=INSTALLATION,
                        trust=signer.trust, now=NOW)
    assert view.status == expected
    assert view.authorized == (expected in (LicenseStatus.ACTIVE, LicenseStatus.OFFLINE_VERIFIED))
    assert view.label and (view.message or view.authorized)


def test_published_state_cannot_grant_but_can_downgrade(signer):
    forged = state(licenses=[{**LICENSE}], check_in={"status": "valid"})
    assert not compute_view(state=forged, token=None, installation_id=INSTALLATION,
                            trust=signer.trust, now=NOW).authorized
    revoked = state(check_in={"status": "revoked", "reason": "refunded",
                              "at": utc_now_iso(NOW)})
    view = compute_view(state=revoked, token=signer(), installation_id=INSTALLATION,
                        trust=signer.trust, now=NOW)
    assert view.status == LicenseStatus.REVOKED and not view.authorized


def test_renewal_warning_and_details(signer):
    view = compute_view(state=state(licenses=[LICENSE], check_in={"status": "valid"}),
                        token=signer(exp=NOW + 3 * 86400), installation_id=INSTALLATION,
                        trust=signer.trust, presence={"heartbeat_at": NOW - 2}, now=NOW)
    assert any("Renewal due" in w for w in view.warnings)
    assert view.tier == "Commercial" and view.devices_active == 1 and view.device_limit == 3
    assert view.companion_running and view.license_source == "verified entitlement"
    stale = compute_view(state=None, token=None, installation_id=None, trust=TrustStore({}, frozenset()),
                         presence={"heartbeat_at": NOW - 60}, now=NOW)
    assert not stale.companion_running


def test_read_view_handles_corrupted_cache(tmp_path, signer):
    store = LocalStore(tmp_path)
    store.installation_id()
    (tmp_path / "state.json").write_text("{corrupt", encoding="utf-8")
    os.chmod(tmp_path / "state.json", 0o600)
    (tmp_path / "entitlement").write_text("garbage", encoding="utf-8")
    os.chmod(tmp_path / "entitlement", 0o600)
    view = read_view(store, trust=signer.trust, now=NOW)
    assert view.status == LicenseStatus.INVALID and not view.authorized
