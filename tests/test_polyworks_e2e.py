# SPDX-License-Identifier: GPL-3.0-or-later
"""The 15-step workflow end to end: Companion service over real HTTP against the
isolated contract backend, Blender status through the registered add-on (fake bpy).
"""
from __future__ import annotations

import json
import sys

import pytest

pytest.importorskip("cryptography")

from cloth_next.polyworks.service import AccountService, ServiceError  # noqa: E402
from cloth_next.polyworks.status import LicenseStatus  # noqa: E402
from cloth_next.polyworks.storage import LocalStore  # noqa: E402
from cloth_next.polyworks.vault import UserFileVault  # noqa: E402
from tests.polyworks_backend import ContractBackend  # noqa: E402


class FakeMonotonic:
    value = 0.0

    def __call__(self):
        return self.value


def blender_view(env):
    module = sys.modules["cloth_next.blender.account_status"]
    module._tick()
    if module._state["worker"] is not None:
        module._state["worker"].join(timeout=5)
    return module.current_view()


def test_complete_workflow(blender_env, tmp_path, monkeypatch):
    backend = ContractBackend().start()
    try:
        root = tmp_path / "account"
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps({"keys": [{"key_id": backend.key_id,
                                                    "public_key_pem": backend.public_key_pem}]}))
        monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(root))
        monkeypatch.setenv("CLOTH_NEXT_POLYWORKS_API_URL", backend.url)
        monkeypatch.setenv("CLOTH_NEXT_POLYWORKS_TRUST_FILE", str(trust_file))
        user = backend.add_user("e2e@example.com")
        license_id = backend.add_license(user, tier="commercial", seats=2)
        clock = FakeMonotonic()

        # 1. Launch Companion (fresh profile).
        companion = AccountService(LocalStore(root), vault=UserFileVault(root), monotonic=clock)
        assert companion.view().status == LicenseStatus.NOT_SIGNED_IN
        # 2. Sign in through browser approval.
        link = companion.start_device_link()
        backend.approve(link.user_code, user)
        clock.value += link.interval
        assert companion.poll_device_link(link)
        # 3. Retrieve owned licences.
        assert [item["id"] for item in companion.state()["licenses"]] == [license_id]
        # 4. Activate the current device.  5. Verify the signed licence.
        assert companion.activate(license_id, "E2E workstation").authorized
        # 6. Launch Blender.  7. Blender displays the correct status.
        blender_env.registration.register()
        view = blender_view(blender_env)
        assert view.status == LicenseStatus.ACTIVE and view.tier == "Commercial"
        assert view.account_identity == "e2e@example.com" and view.seats == 2
        # 8. Simulate server disconnection.  9. Offline behaviour.
        backend.offline = True
        with pytest.raises(ServiceError):
            companion.refresh()
        view = blender_view(blender_env)
        assert view.status == LicenseStatus.OFFLINE_VERIFIED and view.authorized
        # 10. Restore connectivity.
        backend.offline = False
        companion.refresh()
        assert blender_view(blender_env).status == LicenseStatus.ACTIVE
        # 11. Suspend the licence in the isolated backend.  12. Companion detects it.
        backend.set_license_state(license_id, state="suspended")
        assert companion.refresh().status == LicenseStatus.SUSPENDED
        # 13. Blender reflects the new state.
        view = blender_view(blender_env)
        assert view.status == LicenseStatus.SUSPENDED and not view.authorized
        # Recovery: the licence is reinstated and the device re-activated.
        backend.set_license_state(license_id, state="active")
        assert companion.activate(license_id, "E2E workstation").authorized
        # 14. Sign out (releasing the device).  15. Session revoked, local status updated.
        companion.sign_out(release_device=True)
        assert all(session["ended"] for session in backend.sessions.values())
        view = blender_view(blender_env)
        assert view.status == LicenseStatus.NOT_SIGNED_IN and not view.authorized
        assert not any(a["active"] for a in backend.activations.values())
        # Blender never contacted the backend: every request came from the Companion.
        assert all(path.startswith("/v1/") for _m, path, _b, _a in backend.requests)
        blender_env.registration.unregister()
    finally:
        backend.stop()


def test_companion_restart_and_multiple_blender_readers(blender_env, tmp_path, monkeypatch):
    backend = ContractBackend().start()
    try:
        root = tmp_path / "account"
        trust_file = tmp_path / "trust.json"
        trust_file.write_text(json.dumps({"keys": [{"key_id": backend.key_id,
                                                    "public_key_pem": backend.public_key_pem}]}))
        monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(root))
        monkeypatch.setenv("CLOTH_NEXT_POLYWORKS_TRUST_FILE", str(trust_file))
        from cloth_next.polyworks.api import Endpoints
        from cloth_next.polyworks.status import read_view
        from cloth_next.polyworks.trust import load_trust
        user = backend.add_user("restart@example.com", "pw")
        license_id = backend.add_license(user)
        endpoints = Endpoints(backend.url, backend.url)
        first = AccountService(LocalStore(root), endpoints=endpoints, vault=UserFileVault(root))
        first.login_password("restart@example.com", "pw")
        first.activate(license_id, "Restart")
        # Blender started before the Companion restarts; a second Blender reads too.
        blender_env.registration.register()
        assert blender_view(blender_env).authorized
        assert read_view(LocalStore(root), trust=load_trust()).authorized
        # Companion "restarts": a new process reuses the stored session and id.
        second = AccountService(LocalStore(root), endpoints=endpoints, vault=UserFileVault(root))
        assert second.refresh().status == LicenseStatus.ACTIVE
        assert sum(a["active"] for a in backend.activations.values()) == 1
        # Corrupted cache: the state file is replaced with garbage.
        (root / "state.json").write_text("{garbage", encoding="utf-8")
        view = blender_view(blender_env)
        assert view.authorized  # the verified entitlement still stands on its own
        assert second.refresh().status == LicenseStatus.ACTIVE  # Companion rewrites it
        blender_env.registration.unregister()
    finally:
        backend.stop()
