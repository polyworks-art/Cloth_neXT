# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender shows PolyWorks status read-only and never talks to the API."""
from __future__ import annotations

import ast
import builtins
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace

import pytest

from tests import fake_bpy
from tests.test_developer_tools_ui import RecordingLayout

ROOT = Path(__file__).resolve().parents[1]
# Since 2026-10-18 (direct GAIA workflow) exactly one Blender module may reach the
# PolyWorks API: the account/GAIA worker, and only from its worker thread.
GAIA_WORKER = ROOT / "cloth_next" / "blender" / "gaia_account.py"
BLENDER_SIDE = [*sorted(path for path in (ROOT / "cloth_next" / "blender").rglob("*.py")
                        if path != GAIA_WORKER),
                *[ROOT / "cloth_next" / "polyworks" / f"{name}.py" for name in
                  ("status", "state", "entitlements", "storage", "trust", "ed25519", "messages")]]
FORBIDDEN_MODULES = {"cloth_next.polyworks.api", "cloth_next.polyworks.service",
                     "cloth_next.polyworks.vault", "urllib.request",
                     "http.client", "requests", "keyring"}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parent, base] if base else parent)
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("path", BLENDER_SIDE, ids=lambda p: p.name)
def test_blender_side_never_imports_api_client_or_vault(path):
    assert not imported_modules(path) & FORBIDDEN_MODULES


def test_gaia_worker_imports_the_api_only_inside_functions():
    tree = ast.parse(GAIA_WORKER.read_text(encoding="utf-8"))
    top_level = {alias.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
                 for alias in node.names} | {node.module or "" for node in tree.body
                                             if isinstance(node, ast.ImportFrom)}
    assert not any(name in {"api", "service", "vault", "http.client", "urllib.request"}
                   or "polyworks" in name for name in top_level)


def test_blender_side_never_references_session_vaults():
    for path in (ROOT / "cloth_next" / "blender").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in ("default_vault", "UserFileVault", "KeyringVault", "WindowsDpapiVault",
                     "Authorization", "Bearer"):
            assert name not in text, (path, name)


def test_preferences_have_no_account_properties(blender_env):
    props = fake_bpy._resolved_props(blender_env.registration.preferences.CLOTHNEXT_AddonPreferences)
    for name in props:
        assert not any(word in name.lower() for word in
                       ("email", "password", "token", "license", "licence", "account",
                        "api", "activation", "seat", "mfa", "code"))


@pytest.fixture
def account_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOTH_NEXT_ACCOUNT_ROOT", str(tmp_path / "account"))
    return tmp_path / "account"


@pytest.fixture
def signed(account_root, monkeypatch):
    pytest.importorskip("cryptography")
    from tests.polyworks_backend import ContractBackend
    import json
    backend = ContractBackend(key_id="kid-blender")
    trust_file = account_root.parent / "trust.json"
    trust_file.write_text(json.dumps({"keys": [{"key_id": "kid-blender",
                                                "public_key_pem": backend.public_key_pem}]}))
    monkeypatch.setenv("CLOTH_NEXT_POLYWORKS_TRUST_FILE", str(trust_file))
    from cloth_next.polyworks.storage import LocalStore
    store = LocalStore(account_root)
    installation = store.installation_id()
    now = int(time.time())

    def write(**overrides):
        payload = {"typ": "polyworks.entitlement", "v": 1, "kid": "kid-blender", "lic": "lic-1",
                   "sub": "u", "act": "a", "inst": installation, "prd": "cloth-next",
                   "tier": "commercial", "seats": 2, "seat": "s", "iat": now - 5,
                   "exp": now + 20 * 86400, **overrides}
        store.write_entitlement(backend.sign(payload))
    write.store = store
    return write


def run_read(module):
    module._tick()
    worker = module._state["worker"]
    if worker is not None:
        worker.join(timeout=5)
    module._tick()  # main-thread part: redraw tagging, sync decisions
    return module.current_view()


def account_module(env):
    return sys.modules["cloth_next.blender.account_status"]


def test_registration_reload_and_unregister_leave_no_timers(blender_env, account_root):
    module = account_module(blender_env)
    blender_env.registration.register()
    timers = blender_env.bpy.app.timers.functions
    assert timers.count(module._tick) == 1
    module.register()  # reload path: never duplicates the timer
    assert timers.count(module._tick) == 1
    blender_env.registration.unregister()
    assert module._tick not in timers
    assert module._state["worker"] is None and module.current_view() is None
    assert not account_root.exists()  # registration performs no file IO


def test_status_is_read_in_background_and_shows_verified_license(blender_env, signed, monkeypatch):
    module = account_module(blender_env)
    module.register()
    signed()

    def no_network(*_args, **_kwargs):
        raise AssertionError("Blender must not open network connections")
    monkeypatch.setattr(socket, "socket", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    view = run_read(module)
    assert view.authorized and view.status == "OFFLINE_VERIFIED"
    assert view.tier == "Commercial" and view.development_trust
    assert module.authorized()
    module.unregister()


def test_published_change_updates_view_and_revocation_is_reflected(blender_env, signed):
    module = account_module(blender_env)
    module.register()
    signed()
    assert run_read(module).authorized
    signed.store.delete_entitlement()  # Companion check-in reported revoked
    signed.store.write_json("state.json", {"account": {"signed_in": True, "session": "active"},
                                           "check_in": {"status": "revoked",
                                                        "reason": "refunded"}})
    view = run_read(module)
    assert view.status == "REVOKED" and not view.authorized
    module.unregister()


def test_draw_is_informational_and_does_no_io(blender_env, signed, monkeypatch):
    module = account_module(blender_env)
    module.register()
    signed()
    view = run_read(module)
    prefs = blender_env.registration.preferences
    layout = RecordingLayout()
    prop_calls = []
    layout.prop = lambda *a, **k: prop_calls.append(a)
    real_open = builtins.open

    def no_io(*args, **kwargs):
        raise AssertionError("draw must not read files")
    monkeypatch.setattr(builtins, "open", no_io)
    try:
        prefs.draw_account_card(layout, SimpleNamespace(connected=True, purchase_validated=False), view)
    finally:
        monkeypatch.setattr(builtins, "open", real_open)
    # Connect/Disconnect start the background account worker; draw itself does no I/O.
    assert set(layout.operators) <= {"clothnext.open_account_companion", "clothnext.polyworks_connect",
                                     "clothnext.polyworks_disconnect"}
    assert "clothnext.open_account_companion" in layout.operators
    assert not prop_calls
    labels = "\n".join(layout.labels)
    for expected in ("Account & License", "Superhive", "Connected", "Not validated",
                     "PolyWorks Services", "Service license", "Offline – verified · Commercial",
                     "This computer", "Activated", "Connection", "development key",
                     "Optional · not needed for Classic simulation"):
        assert expected in labels
    assert "claim" not in labels.lower()
    for forbidden in ("Sign in", "Sign out", "Log in", "Deactivate", "Redeem", "Password"):
        assert not any(label.startswith(forbidden) for label in layout.labels)
    empty = RecordingLayout()
    prefs.draw_account_card(empty, SimpleNamespace(connected=False, purchase_validated=False), None)
    assert empty.operators == [] and "Checking account status…" in empty.labels
    module.unregister()


def test_operators_in_account_section_cannot_mutate_accounts(blender_env):
    module = account_module(blender_env)
    assert [cls.bl_idname for cls in module.CLASSES] == ["clothnext.open_account_companion"]
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert '"account-sync"' in source and '"account"' in source
    for word in ("--sign-out", "--activate", "--deactivate", "--redeem", "logout"):
        assert word not in source


def test_open_companion_launches_account_mode_only(blender_env, account_root, monkeypatch):
    module = account_module(blender_env)
    launched = []

    class Process:
        def poll(self):
            return None

        def terminate(self):
            launched.append("terminated")

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(module, "_companion_command", lambda mode: ["companion", "--mode", mode])
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **_kw: launched.append(command) or Process())
    blender_env.bpy.context.preferences.extensions = SimpleNamespace(repos=[])
    ok, _message = module.open_companion()
    assert ok and launched[0] == ["companion", "--mode", "account"]
    ok, message = module.open_companion()
    assert ok and "already open" in message and len(launched) == 1
    module.unregister()
    assert launched[-1] == "terminated"


def test_headless_sync_only_when_due_signed_in_idle_and_interactive(blender_env, signed, monkeypatch):
    module = account_module(blender_env)
    spawned = []

    class Process:
        def poll(self):
            return 0
    monkeypatch.setattr(module, "_companion_command", lambda mode: ["companion", "--mode", mode])
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **_kw: spawned.append(command) or Process())
    module.register()
    module._state["registered_at"] -= 60
    signed()
    signed.store.write_json("state.json", {"account": {"signed_in": True, "session": "active"},
                                           "connection": {"next_check_at": "2000-01-01T00:00:00Z"}})
    blender_env.bpy.app.background = True
    run_read(module)
    assert spawned == []  # never in background Blender
    blender_env.bpy.app.background = False
    monkeypatch.setattr(module, "_bake_active", lambda: True)
    module._tick()
    assert spawned == []  # never during a Bake (contract 4.3a)
    monkeypatch.setattr(module, "_bake_active", lambda: False)
    module._tick()
    assert spawned == [["companion", "--mode", "account-sync"]]
    module._tick()
    assert len(spawned) == 1  # spacing prevents repeated launches
    module.unregister()


def test_no_sync_when_not_due_or_signed_out(blender_env, signed, monkeypatch):
    module = account_module(blender_env)
    spawned = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda command, **_kw: spawned.append(command))
    monkeypatch.setattr(module, "_bake_active", lambda: False)
    module.register()
    module._state["registered_at"] -= 60
    signed.store.write_json("state.json", {"account": {"signed_in": True, "session": "active"},
                                           "connection": {"next_check_at": "2999-01-01T00:00:00Z"}})
    run_read(module)
    signed.store.write_json("state.json", {"account": {"signed_in": False}})
    run_read(module)
    assert spawned == []
    module.unregister()


def test_companion_unavailable_never_raises(blender_env, signed, monkeypatch):
    module = account_module(blender_env)
    monkeypatch.setattr(module, "_bake_active", lambda: False)

    def missing(_mode):
        raise OSError("Companion bundle missing")
    monkeypatch.setattr(module, "_companion_command", missing)
    module.register()
    module._state["registered_at"] -= 60
    signed.store.write_json("state.json", {"account": {"signed_in": True, "session": "active"}})
    run_read(module)
    assert module.open_companion()[0] is False
    module.unregister()
