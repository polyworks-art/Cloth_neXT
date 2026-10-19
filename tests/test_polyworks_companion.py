# SPDX-License-Identifier: GPL-3.0-or-later
"""Companion account window and account modes against the isolated contract backend."""
from __future__ import annotations

import json
import gc
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
    # Tk objects must be finalized on the UI thread, before another test's
    # account worker can trigger cyclic collection of a previous interpreter.
    gc.collect()
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
    gc.collect()


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
    # Product close remains nonblocking. Tests must finish their worker before
    # destroying the shared Tk interpreter or starting the next test.
    window.pool.shutdown(wait=True, cancel_futures=True)


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
    # The device list reloads on its own; activating again is not offered.
    assert [d["label"] for d in account.devices] == ["UI Test PC"]
    assert not button(account, "Activate this computer").enabled
    assert "is activated" in account.activation_hint.get()
    monkeypatch.setattr(account_window.messagebox, "askyesno", lambda *a, **k: True)
    account.deactivate(account.devices[0])
    idle(account)
    assert account.service.store.read_entitlement() is None
    monkeypatch.setattr(account_window.messagebox, "askyesnocancel", lambda *a, **k: False)
    account.sign_out()
    idle(account)
    assert account.account_status.get().startswith("Not signed in")
    assert account.devices == [] and not account.device_list.winfo_children()
    assert str(account.code_entry.cget("state")) == "disabled"
    assert str(account.label_entry.cget("state")) == "disabled"


def button(window, label):
    return next(b for b, _ in window.controls if b.label == label)


def test_window_logic_follows_account_state(account, backend):
    idle(account)
    # Signed out: nothing to refresh, no account-only input.
    for label in ("Refresh", "Activate this computer", "Redeem code", "Reload devices", "Sign out"):
        assert not button(account, label).enabled, label
    assert button(account, "Sign in with browser").enabled
    assert str(account.code_entry.cget("state")) == "disabled"
    user = backend.add_user("logic@example.com", "pw")
    good = backend.add_license(user, tier="commercial")
    bad = backend.add_license(user, tier="personal")
    backend.set_license_state(bad, state="suspended")
    account.toggle_password()
    account.email.set("logic@example.com")
    account.password.set("pw")
    account.sign_in_password()
    idle(account)
    assert account.message_label.cget("fg") == account_window_module().OK
    # Only the usable licence is selectable and selected; its devices load by themselves.
    radios = {w.cget("value"): w for w in account.license_list.winfo_children()}
    assert str(radios[bad].cget("state")) == "disabled"
    assert str(radios[good].cget("state")) == "normal"
    assert account.selected_license.get() == good
    assert account.devices_for == good and account.devices == []
    assert button(account, "Activate this computer").enabled
    assert button(account, "Refresh").enabled
    assert str(account.code_entry.cget("state")) == "normal"
    # A new action clears the previous message.
    account.code.set("")
    account.submit(lambda: None)
    assert account.message.get() == ""
    idle(account)


def account_window_module():
    from companion import account_window
    return account_window


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


def test_status_texts_point_inside_the_window():
    from cloth_next.polyworks import messages, status
    from companion.account_window import in_window
    texts = [messages.SESSION_EXPIRED, messages.REVOKED, messages.DEVICE_LIMIT_BLENDER,
             *messages.COMPANION.values()]
    view = status.compute_view(state=None, token=None, installation_id=None,
                               trust=load_trust({}))
    texts.append(view.message)
    for text in texts:
        assert "Open Cloth NeXt Companion" not in in_window(text)
        assert "in Cloth NeXt Companion" not in in_window(text)


def test_offline_refresh_keeps_last_known_devices(account, backend):
    idle(account)
    user = backend.add_user("offline@example.com", "pw")
    license_id = backend.add_license(user)
    account.service.login_password("offline@example.com", "pw")
    account.refresh()
    idle(account)
    account.activate()
    idle(account)
    assert len(account.devices) == 1
    backend.offline = True
    account.refresh()
    idle(account)
    assert len(account.devices) == 1 and account.devices_failed
    assert "could not be loaded" in account.devices_hint.get()
    assert account.message_label.cget("fg") != account_window_module().BAD
    assert account.selected_license.get() == license_id
