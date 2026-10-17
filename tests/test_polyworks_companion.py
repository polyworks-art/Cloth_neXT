# SPDX-License-Identifier: GPL-3.0-or-later
"""Companion account window and account modes against the isolated contract backend."""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("cryptography")
tk = pytest.importorskip("tkinter")

from cloth_next.polyworks.api import Endpoints  # noqa: E402
from cloth_next.polyworks.service import AccountService  # noqa: E402
from cloth_next.polyworks.status import LicenseStatus  # noqa: E402
from cloth_next.polyworks.storage import LocalStore  # noqa: E402
from cloth_next.polyworks.trust import load_trust  # noqa: E402
from cloth_next.polyworks.vault import UserFileVault  # noqa: E402
from tests.polyworks_backend import ContractBackend  # noqa: E402


@pytest.fixture
def backend():
    server = ContractBackend().start()
    yield server
    server.stop()


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError:
        pytest.skip("no display for Tk")
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def account(backend, tmp_path, root, monkeypatch):
    from companion import account_window
    opened = []
    monkeypatch.setattr(account_window.webbrowser, "open", lambda url, new=0: opened.append(url))
    store = LocalStore(tmp_path / "account")
    service = AccountService(store, endpoints=Endpoints(backend.url, backend.url),
                             vault=UserFileVault(store.root),
                             trust=load_trust({}, production=backend.trust_pems()))
    window = account_window.AccountWindow(root, service=service)
    window.opened = opened
    yield window
    window.close()


def pump(window, condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window.root.update()
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def idle(window):
    pump(window, lambda: not window.busy and window.events.empty())


def test_browser_sign_in_activation_devices_and_sign_out(account, backend, monkeypatch):
    from companion import account_window
    idle(account)
    assert account.service.store.read_json("companion.json")["mode"] == "account"
    user = backend.add_user("ui@example.com")
    license_id = backend.add_license(user)
    account.sign_in_browser()
    pump(account, lambda: account.link is not None)
    assert account.opened[-1].startswith(backend.url + "/link?code=")
    assert account.link_code.get() == account.link.user_code
    backend.approve(account.link.user_code, user)
    pump(account, lambda: account.link is None and not account.busy)
    assert account.account_status.get().startswith("Signed in  ·  ui@example.com")
    assert account.selected_license.get() == license_id
    account.device_label.set("UI Test PC")
    account.activate()
    idle(account)
    assert account.view.status == LicenseStatus.ACTIVE
    assert "Active" in account.license_status.get()
    account.load_devices()
    idle(account)
    assert [d["label"] for d in account.devices] == ["UI Test PC"]
    monkeypatch.setattr(account_window.messagebox, "askyesno", lambda *a, **k: True)
    account.deactivate(account.devices[0])
    idle(account)
    assert account.service.store.read_entitlement() is None
    monkeypatch.setattr(account_window.messagebox, "askyesnocancel", lambda *a, **k: False)
    account.sign_out()
    idle(account)
    assert account.account_status.get().startswith("Not signed in")


def test_password_is_cleared_and_errors_are_human(account, backend):
    idle(account)
    backend.add_user("pw@example.com", "right")
    account.toggle_password()
    account.email.set("pw@example.com")
    account.password.set("wrong")
    account.sign_in_password()
    assert account.password.get() == ""
    idle(account)
    assert "not correct" in account.message.get()
    assert "server text" not in account.message.get()


def test_buttons_are_disabled_while_busy(account, backend):
    idle(account)
    backend.add_user("busy@example.com")
    account.sign_in_browser()
    assert account.busy and not any(button.enabled for button, _ in account.controls)


def test_close_removes_presence(account):
    idle(account)
    presence = account.service.store.path("companion.json")
    assert presence.exists()
    account.close()
    assert not presence.exists()


def test_account_sync_mode_checks_in_headlessly(backend, tmp_path, monkeypatch):
    from companion import app
    user = backend.add_user("sync@example.com")
    license_id = backend.add_license(user)
    root = tmp_path / "account"
    trust_file = tmp_path / "trust.json"
    trust_file.write_text(json.dumps({"keys": [{"key_id": backend.key_id,
                                                "public_key_pem": backend.public_key_pem}]}))
    monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(root))
    monkeypatch.setenv("CLOTH_NEXT_POLYWORKS_API_URL", backend.url)
    monkeypatch.setenv("CLOTH_NEXT_POLYWORKS_TRUST_FILE", str(trust_file))
    service = AccountService(LocalStore(root), vault=UserFileVault(root))
    service.login_password("sync@example.com", "correct horse")
    service.activate(license_id, "Sync")
    first = service.store.read_entitlement()
    state = service.state()
    state["connection"]["next_check_at"] = "2000-01-01T00:00:00Z"
    service.store.write_json("state.json", state)
    monkeypatch.setattr("cloth_next.polyworks.vault.default_vault", lambda r: UserFileVault(r))
    monkeypatch.setattr("cloth_next.polyworks.service.default_vault", lambda r: UserFileVault(r))
    time.sleep(1.1)  # a renewed entitlement gets a later iat
    with pytest.raises(SystemExit) as exit_code:
        app.main(["--mode", "account-sync", "--blender-version", "5.1.2"])
    assert exit_code.value.code == 0
    assert service.store.read_entitlement() != first
    assert any(path == "/v1/entitlements/check-in" for _m, path, _b, _a in backend.requests)
    device_start = [b for m, p, b, _a in backend.requests if p == "/v1/auth/device/start"]
    assert device_start == []


def test_account_modes_reject_bake_transport_arguments():
    from companion import app
    with pytest.raises(SystemExit):
        app.main(["--mode", "account", "--port", "1234"])
    with pytest.raises(SystemExit):
        app.main(["--mode", "welcome", "--blender-version", "5.1"])


def test_client_name_includes_versions():
    from companion import app
    args = type("Args", (), {"blender_version": "5.1.2"})()
    assert app._account_client_name(args).endswith("/ Blender 5.1.2 (Companion)")
    args.blender_version = "5.1\nX-Header: evil"
    assert "evil" not in app._account_client_name(args)
