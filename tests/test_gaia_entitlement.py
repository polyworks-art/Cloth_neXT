# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS GAIA entitlement: issuance, hand-over to the native engine, renewal, revocation.

Runs against the contract test server (not production). Every GAIA access
path is exercised for an account without any Cloth NeXt licence or device
activation. With a test-key engine build (``CLOTH_NEXT_TETHYS_TEST_ENGINE``)
the native engine's own verdict is checked too.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

from cloth_next.polyworks import gaia_downloads  # noqa: E402
from cloth_next.polyworks.api import Endpoints  # noqa: E402
from cloth_next.polyworks.entitlements import EntitlementError, verify_gaia  # noqa: E402
from cloth_next.polyworks.service import AccountService  # noqa: E402
from cloth_next.polyworks.storage import GAIA_ENTITLEMENT_FILE, LocalStore  # noqa: E402
from cloth_next.polyworks.trust import load_trust  # noqa: E402
from cloth_next.polyworks.vault import UserFileVault  # noqa: E402
from tests.polyworks_backend import ContractBackend  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEST_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))   # engine test builds trust this key
TEST_KID = "tethys-test"


class Clock:
    value = 1000.0

    def __call__(self):
        return self.value


@pytest.fixture
def backend(monkeypatch):
    server = ContractBackend(key_id=TEST_KID, private_key=TEST_KEY).start()
    monkeypatch.setenv(gaia_downloads.DOWNLOAD_HOST_ENV, "127.0.0.1")
    yield server
    server.stop()


def _service(backend, root):
    clock = Clock()
    service = AccountService(LocalStore(root), endpoints=Endpoints(backend.url, backend.url),
                             vault=UserFileVault(root), trust=load_trust({}, production=backend.trust_pems()),
                             monotonic=clock, client_name="Cloth NeXt test / Blender")
    user = backend.add_user(f"tester-{os.urandom(3).hex()}@example.com")   # no licence, no activation
    link = service.start_device_link()
    backend.approve(link.user_code, user)
    clock.value += link.interval
    assert service.poll_device_link(link)
    service.user = user
    return service


def _engine_verdict(account_root, executable):
    """Run the engine on a tiny bake and return its gaia_entitlement status."""
    from tests import test_tethys
    work = account_root.parent / f"bake-{os.urandom(3).hex()}"
    work.mkdir()
    request, _pc2, _owners = test_tethys._scenario(work, frames=3)
    environment = dict(os.environ, CLOTH_NEXT_ACCOUNT_ROOT=str(account_root))
    result = subprocess.run([str(executable), "run", str(request)], capture_output=True, text=True,
                            timeout=60, env=environment)
    events = [json.loads(line[7:]) for line in result.stdout.splitlines() if line.startswith("TETHYS ")]
    start = next(e for e in events if e["event"] == "start")
    assert result.returncode == 0, events          # never blocks while enforcement is off
    assert start["enforced"] is False
    return start["gaia_entitlement"]


def _test_engine():
    explicit = os.environ.get("CLOTH_NEXT_TETHYS_TEST_ENGINE", "")
    default = ROOT.parent / "TETHYS-Engine" / "build" / "windows-x64" / "tethys-engine-test.exe"
    path = Path(explicit) if explicit else default
    return path if path.is_file() else None


ACCESS_PATHS = ("approved_request", "invite", "dev_tester", "everyone")


def _grant(backend, service, path):
    if path == "approved_request":
        assert service.request_gaia_access() == "pending"
        assert service.refresh_gaia_entitlement() == "removed"     # pending: nothing issued
        backend.gaia_access[service.user] = "granted"               # administrator approves
    elif path == "invite":
        backend.gaia_invites["invite-token"] = 1
        assert service.redeem_gaia_invite("https://account.cloth-next.de/gaia-invite#invite-token")
    elif path == "dev_tester":
        backend.gaia_testers.add(service.user)
    elif path == "everyone":
        backend.gaia_mode = "everyone"


@pytest.mark.parametrize("path", ACCESS_PATHS)
def test_every_access_path_issues_an_engine_verified_entitlement(backend, tmp_path, path):
    root = tmp_path / "account"
    service = _service(backend, root)
    assert not service.state()["licenses"] and not service.store.read_entitlement()   # no licence at all
    _grant(backend, service, path)
    assert service.gaia_access()["status"] == "granted"
    assert service.refresh_gaia_entitlement() == "stored"
    token = (root / GAIA_ENTITLEMENT_FILE).read_text("utf-8")
    claims = verify_gaia(token, trust=service.trust, installation_id=service.installation_id())
    assert claims.features == ("gaia",) and claims.subject == service.user
    payload = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=" * (-len(token.split(".")[0]) % 4)))
    assert payload["typ"] == "polyworks.entitlement" and payload["purpose"] == "gaia-access"
    assert claims.expires_at - claims.issued_at == 7 * 24 * 3600
    engine = _test_engine()
    if engine is not None:
        assert _engine_verdict(root, engine) == "valid"


def test_renewal_revocation_and_suspension(backend, tmp_path):
    root = tmp_path / "account"
    service = _service(backend, root)
    backend.gaia_access[service.user] = "granted"
    assert service.refresh_gaia_entitlement() == "stored"
    first = (root / GAIA_ENTITLEMENT_FILE).read_text("utf-8")
    real_clock = backend.clock
    backend.clock = lambda: real_clock() + 3 * 86400            # three days later
    service.clock = backend.clock
    assert service.refresh_gaia_entitlement() == "stored"
    renewed = (root / GAIA_ENTITLEMENT_FILE).read_text("utf-8")
    assert renewed != first
    claims = verify_gaia(renewed, trust=service.trust, installation_id=service.installation_id(),
                         now=real_clock() + 3 * 86400)
    assert claims.issued_at >= int(real_clock()) + 3 * 86400 - 5
    backend.clock = service.clock = real_clock
    backend.revoke_gaia(service.user)
    assert service.refresh_gaia_entitlement() == "removed"
    assert not (root / GAIA_ENTITLEMENT_FILE).exists()
    engine = _test_engine()
    if engine is not None:
        assert _engine_verdict(root, engine) == "missing"
    backend.gaia_access[service.user] = "granted"
    assert service.refresh_gaia_entitlement() == "stored"
    backend.suspended.add(service.user)
    assert service.refresh_gaia_entitlement() == "removed"
    assert not (root / GAIA_ENTITLEMENT_FILE).exists()


def test_transition_phase_never_blocks(backend, tmp_path):
    """Server without the endpoint, or offline: nothing is removed or blocked."""
    root = tmp_path / "account"
    service = _service(backend, root)
    backend.gaia_access[service.user] = "granted"
    backend.gaia_entitlement_route = False                 # older server: no issuing endpoint
    assert service.refresh_gaia_entitlement() == "unavailable"
    assert not (root / GAIA_ENTITLEMENT_FILE).exists()
    assert service.gaia_access()["status"] == "granted"    # GAIA itself is unaffected
    backend.gaia_entitlement_route = True
    assert service.refresh_gaia_entitlement() == "stored"
    backend.offline = True
    assert service.refresh_gaia_entitlement() == "kept"
    assert (root / GAIA_ENTITLEMENT_FILE).exists()


def test_installation_binding_and_licence_tokens_are_refused(backend, tmp_path):
    root = tmp_path / "account"
    service = _service(backend, root)
    backend.gaia_access[service.user] = "granted"
    assert service.refresh_gaia_entitlement() == "stored"
    token = (root / GAIA_ENTITLEMENT_FILE).read_text("utf-8")
    with pytest.raises(EntitlementError):
        verify_gaia(token, trust=service.trust, installation_id="inst_someone_else")
    licence = backend.sign({"typ": "polyworks.entitlement", "v": 1, "kid": TEST_KID, "lic": "l",
                            "sub": service.user, "act": "a", "inst": service.installation_id(),
                            "prd": "cloth-next", "tier": "commercial", "seats": 1, "seat": "s",
                            "iat": 1, "exp": 4102444800})
    with pytest.raises(EntitlementError):
        verify_gaia(licence, trust=service.trust, installation_id=service.installation_id())
    engine = _test_engine()
    if engine is not None:
        (root / GAIA_ENTITLEMENT_FILE).write_text(licence, encoding="utf-8")
        assert _engine_verdict(root, engine) == "wrong_purpose"


def test_production_engine_trusts_only_the_pinned_key(backend, tmp_path):
    from cloth_next.tethys import engine as tethys_engine
    try:
        production = tethys_engine.locate()
    except tethys_engine.EngineUnavailable:
        pytest.skip("no production engine staged")
    root = tmp_path / "account"
    service = _service(backend, root)
    backend.gaia_access[service.user] = "granted"
    assert service.refresh_gaia_entitlement() == "stored"
    assert _engine_verdict(root, production) == "unknown_key"
