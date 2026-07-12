# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fake-bpy tests for the Cloth NeXt add-on update UI and operators.

These tests verify Cloth NeXt's own logic only: repository resolution by
remote URL, the directory-based operator arguments it passes, call order,
fallback behavior, and solver/companion separation. They deliberately make
no claim about real Blender operator context compatibility — that is covered
by ``tools/blender_update_smoke_test.py`` running inside real Blender 5.1.2.
"""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.updater.addon_updates import AddonUpdateState, UpdateChannel
from cloth_next.updater.addon_versions import parse_version

REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_PACKAGE = REPO_ROOT / "cloth_next" / "blender"
BETA_URL = UpdateChannel.BETA.index_url
STABLE_URL = UpdateChannel.STABLE.index_url


def set_channel(env, channel="BETA"):
    env.bpy.context.preferences.addons["cloth_next"] = SimpleNamespace(
        preferences=SimpleNamespace(update_channel=channel))


def add_repo(env, url, enabled=True, directory="/fake/extensions/mod"):
    env.bpy.context.preferences.extensions.repos.append(SimpleNamespace(
        name="repo", module="mod", remote_url=url, enabled=enabled,
        use_remote_url=True, directory=directory))


def updater(env):
    return env.addon_update_operators


def run_check(env):
    op = updater(env).CLOTHNEXT_OT_addon_update_check()
    result = op.execute(env.bpy.context)
    return op, result


class FakeLayout:
    def __init__(self, log=None):
        self.log = log if log is not None else []
        self.enabled = True

    def box(self):
        self.log.append(("box",))
        return FakeLayout(self.log)

    def row(self, align=False):
        return FakeLayout(self.log)

    def column(self, align=False):
        return FakeLayout(self.log)

    def label(self, text="", icon=None):
        self.log.append(("label", text))

    def operator(self, idname, text=None, icon=None):
        self.log.append(("operator", idname))
        return SimpleNamespace()

    def prop(self, _data, prop_name):
        self.log.append(("prop", prop_name))


# --- repository setup is explicit and never duplicated (items 8+9) ------------------

def test_register_never_creates_repositories_or_calls_operators(blender_env):
    env = blender_env
    env.registration.register()
    assert env.bpy.context.preferences.extensions.repos == []
    assert env.bpy.ops_log == []
    env.registration.unregister()


def test_repo_setup_adds_channel_repository_once(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env, "BETA")
    op = updater(env).CLOTHNEXT_OT_addon_update_repo_setup()
    assert op.execute(env.bpy.context) == {"FINISHED"}
    repos = env.bpy.context.preferences.extensions.repos
    assert len(repos) == 1 and repos[0].remote_url == BETA_URL
    assert env.bpy.ops_log[-1][0] == "preferences.extension_repo_add"
    # second click: no duplicate
    op2 = updater(env).CLOTHNEXT_OT_addon_update_repo_setup()
    assert op2.execute(env.bpy.context) == {"CANCELLED"}
    assert len(repos) == 1
    env.registration.unregister()


def test_repo_setup_detects_existing_repo_with_trailing_slash(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env, "BETA")
    add_repo(env, BETA_URL + "/")
    op = updater(env).CLOTHNEXT_OT_addon_update_repo_setup()
    assert op.execute(env.bpy.context) == {"CANCELLED"}
    assert len(env.bpy.context.preferences.extensions.repos) == 1
    env.registration.unregister()


# --- online access (item 10) ---------------------------------------------------------

def test_check_blocked_when_online_access_disabled(blender_env):
    env = blender_env
    env.registration.register()
    env.bpy.app.online_access = False
    set_channel(env)
    add_repo(env, BETA_URL)
    op, result = run_check(env)
    assert result == {"CANCELLED"}
    assert updater(env).session().state is AddonUpdateState.ONLINE_ACCESS_DISABLED
    assert any("WARNING" in levels for levels, _m in op.reports)
    env.registration.unregister()


def test_install_blocked_when_online_access_disabled(blender_env):
    env = blender_env
    env.registration.register()
    env.bpy.app.online_access = False
    set_channel(env)
    add_repo(env, BETA_URL)
    updater(env).session().state = AddonUpdateState.UPDATE_AVAILABLE
    op = updater(env).CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"CANCELLED"}
    assert updater(env).session().state is AddonUpdateState.ONLINE_ACCESS_DISABLED
    env.registration.unregister()


# --- no network during import, register, or draw (item 11) ---------------------------

def test_no_network_during_register_and_draw(blender_env, monkeypatch):
    import socket
    import urllib.request

    def bomb(*_a, **_k):
        raise AssertionError("network access during register/draw")

    monkeypatch.setattr(socket, "socket", bomb)
    monkeypatch.setattr(socket, "create_connection", bomb)
    monkeypatch.setattr(urllib.request, "urlopen", bomb)
    env = blender_env
    env.registration.register()
    import cloth_next.blender.preferences as preferences
    monkeypatch.setattr(preferences, "_safe_read_current", lambda: (None, True))
    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.layout = FakeLayout()
    prefs.external_solver_path = ""
    prefs.update_channel = "BETA"
    prefs.draw(env.bpy.context)  # must not touch the network
    assert prefs.layout.log  # something was drawn
    env.registration.unregister()


# --- check behavior (items 12, 13, 14) -----------------------------------------------

def test_check_without_repository_reports_not_configured(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env, "BETA")
    add_repo(env, STABLE_URL)  # only the WRONG channel is configured
    op, _result = run_check(env)
    assert updater(env).session().state is \
        AddonUpdateState.REPOSITORY_NOT_CONFIGURED
    env.registration.unregister()


def test_check_with_disabled_repository_reports_disabled(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env, "BETA")
    add_repo(env, BETA_URL, enabled=False)
    run_check(env)
    assert updater(env).session().state is AddonUpdateState.REPOSITORY_DISABLED
    env.registration.unregister()


def test_repeated_check_clicks_do_not_start_duplicate_workers(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    gate = threading.Event()
    started = []

    def slow_check(session, _channel, _installed, fetch=None):
        started.append(1)
        gate.wait(timeout=10)
        session.state = AddonUpdateState.UP_TO_DATE

    from cloth_next.updater import addon_updates as pure
    monkeypatch.setattr(pure, "run_update_check", slow_check)
    _op1, result1 = run_check(env)
    assert result1 == {"FINISHED"}
    assert updater(env).session().state is AddonUpdateState.CHECKING
    op2, result2 = run_check(env)
    assert result2 == {"CANCELLED"}
    gate.set()
    updater(env)._worker.join(timeout=10)
    assert started == [1]
    assert updater(env).session().state is AddonUpdateState.UP_TO_DATE
    env.registration.unregister()


def test_check_timer_lifecycle_and_unregister_cleanup(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    from cloth_next.updater import addon_updates as pure
    monkeypatch.setattr(pure, "run_update_check",
                        lambda session, _c, _i, fetch=None: setattr(
                            session, "state", AddonUpdateState.UP_TO_DATE))
    run_check(env)
    pulse = updater(env)._ui_refresh_pulse
    assert pulse in env.bpy.app.timers.functions
    updater(env)._worker.join(timeout=10)
    assert pulse() is None  # worker done: timer asks to stop
    env.registration.unregister()
    assert env.bpy.app.timers.functions == []
    assert updater(env).session().state is AddonUpdateState.NOT_CHECKED


def test_check_maps_newer_version_to_update_available(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    from cloth_next.updater import addon_updates as pure
    original_check = pure.run_update_check

    def fake_check(session, channel, installed, fetch=None):
        original_check(session, channel, installed,
                       fetch=lambda _c: {"data": [
                           {"id": "cloth_next", "version": "9.9.9-beta.1"}]})

    monkeypatch.setattr(pure, "run_update_check", fake_check)
    run_check(env)
    updater(env)._worker.join(timeout=10)
    assert updater(env).session().state is AddonUpdateState.UPDATE_AVAILABLE
    assert str(updater(env).session().latest) == "9.9.9-beta.1"
    env.registration.unregister()


# --- update guard integration (items 15+16 at operator level) ------------------------

def test_install_blocked_in_every_unsafe_application_state(blender_env, monkeypatch):
    from cloth_next.core.state import ApplicationState
    from cloth_next.updater.addon_update_guard import UPDATE_SAFE_STATES
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    module = updater(env)
    for state in ApplicationState:
        if state in UPDATE_SAFE_STATES:
            continue
        module.session().state = AddonUpdateState.UPDATE_AVAILABLE
        monkeypatch.setattr(module, "application_state_provider", lambda s=state: s)
        op = module.CLOTHNEXT_OT_addon_update_install()
        assert op.execute(env.bpy.context) == {"CANCELLED"}, state
        assert module.session().state is AddonUpdateState.INSTALL_BLOCKED
        assert state.name in module.session().message
    assert all(name != "extensions.repo_sync" for name, _kw in env.bpy.ops_log)
    env.registration.unregister()


# --- owned/external solver handling (items 17+18) ------------------------------------

class FakeManager:
    def __init__(self, log, name):
        self.log = log
        self.name = name
        self.running = True

    def stop(self):
        self.running = False
        self.log.append((f"{self.name}.stop", {}))

    def poll(self):
        return SimpleNamespace(running=self.running)


def test_owned_solver_stopped_before_blender_update_external_never(blender_env, monkeypatch):
    from cloth_next.core.state import ApplicationState
    from cloth_next.ppf.models import ConnectionOwnership
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    module = updater(env)
    owned = FakeManager(env.bpy.ops_log, "owned")
    external = FakeManager(env.bpy.ops_log, "external")
    module.register_owned_process_manager(owned, ConnectionOwnership.OWNED_PROCESS)
    module.register_owned_process_manager(external,
                                          ConnectionOwnership.EXTERNAL_SERVER)
    # The application state is update-safe; a leftover owned process must
    # still be stopped (defense in depth), an external server never.
    monkeypatch.setattr(module, "application_state_provider",
                        lambda: ApplicationState.STOPPED)
    module.session().state = AddonUpdateState.UPDATE_AVAILABLE
    op = module.CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"FINISHED"}
    names = [name for name, _kw in env.bpy.ops_log]
    assert "owned.stop" in names
    assert "external.stop" not in names
    assert not external.log or all(n != "external.stop" for n, _ in external.log)
    assert names.index("owned.stop") < names.index("extensions.repo_sync")
    env.registration.unregister()


def test_install_aborts_when_owned_solver_does_not_exit(blender_env):
    from cloth_next.ppf.models import ConnectionOwnership
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    module = updater(env)

    class StuckManager:
        def stop(self):
            pass

        def poll(self):
            return SimpleNamespace(running=True)

    module.register_owned_process_manager(StuckManager(),
                                          ConnectionOwnership.OWNED_PROCESS)
    module.session().state = AddonUpdateState.UPDATE_AVAILABLE
    op = module.CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"CANCELLED"}
    assert module.session().state is AddonUpdateState.INSTALL_BLOCKED
    assert all(name != "extensions.repo_sync" for name, _kw in env.bpy.ops_log)
    env.registration.unregister()


# --- Blender mechanism and fallback (items 19, 20, 21) -------------------------------

def test_install_uses_blenders_extension_mechanism(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    module = updater(env)
    module.session().state = AddonUpdateState.UPDATE_AVAILABLE
    op = module.CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"FINISHED"}
    names = [name for name, _kw in env.bpy.ops_log]
    assert "extensions.repo_sync" in names
    assert "extensions.package_install" in names
    assert names.index("extensions.repo_sync") < names.index(
        "extensions.package_install")
    ops = dict(env.bpy.ops_log)
    # repository identified by its resolved directory, never by an index
    # (repo_index counts only enabled/valid repositories and silently shifts)
    assert ops["extensions.repo_sync"] == {
        "repo_directory": "/fake/extensions/mod"}
    assert ops["extensions.package_install"] == {
        "repo_directory": "/fake/extensions/mod", "pkg_id": "cloth_next",
        "enable_on_install": True}
    assert module.session().state is AddonUpdateState.RESTART_REQUIRED
    env.registration.unregister()


def test_install_never_upgrades_unrelated_extensions(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, "https://example.invalid/other/index.json",
             directory="/fake/extensions/other")
    add_repo(env, BETA_URL)
    module = updater(env)
    module.session().state = AddonUpdateState.UPDATE_AVAILABLE
    op = module.CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"FINISHED"}
    names = [name for name, _kw in env.bpy.ops_log]
    assert "extensions.package_upgrade_all" not in names
    assert "extensions.repo_sync_all" not in names
    for name, kwargs in env.bpy.ops_log:
        if name == "extensions.package_install":
            assert kwargs["pkg_id"] == "cloth_next"
        if name in ("extensions.repo_sync", "extensions.package_install"):
            assert kwargs["repo_directory"] == "/fake/extensions/mod"
    env.registration.unregister()


def test_install_distinguishes_disabled_repository(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL, enabled=False)
    module = updater(env)
    module.session().state = AddonUpdateState.UPDATE_AVAILABLE
    op = module.CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"CANCELLED"}
    assert module.session().state is AddonUpdateState.REPOSITORY_DISABLED
    assert "enable" in module.session().message.lower()
    assert all(name != "extensions.repo_sync" for name, _kw in env.bpy.ops_log)
    env.registration.unregister()


def test_install_reports_sync_failure_distinctly(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    module = updater(env)
    module.session().state = AddonUpdateState.UPDATE_AVAILABLE
    env.bpy.ops.extensions.repo_sync.raises = RuntimeError("connection refused")
    op = module.CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"CANCELLED"}
    assert module.session().state is AddonUpdateState.SYNC_FAILED
    assert "connection refused" in module.session().message
    names = [name for name, _kw in env.bpy.ops_log]
    assert "extensions.package_install" not in names
    env.registration.unregister()


def test_install_falls_back_to_blender_extensions_view(blender_env):
    env = blender_env
    env.registration.register()
    set_channel(env)
    add_repo(env, BETA_URL)
    module = updater(env)
    module.session().state = AddonUpdateState.UPDATE_AVAILABLE
    env.bpy.ops.extensions.package_install.raises = RuntimeError("no UI context")
    op = module.CLOTHNEXT_OT_addon_update_install()
    assert op.execute(env.bpy.context) == {"FINISHED"}
    names = [name for name, _kw in env.bpy.ops_log]
    assert "extensions.repo_sync" in names  # the exact repo was synchronized
    assert "extensions.userpref_show_for_update" in names
    assert module.session().state is AddonUpdateState.UNAVAILABLE
    assert "synchronized" in module.session().message.lower()
    assert "click" in module.session().message.lower()
    env.registration.unregister()


def test_update_module_never_touches_the_solver_installation():
    source = (BLENDER_PACKAGE / "addon_update_operators.py").read_text("utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"shutil", "os", "subprocess", "socket"}
    assert not imported & forbidden, imported & forbidden
    for module_name in imported:
        assert "managed" not in module_name
        assert "install_paths" not in module_name
        assert "download" not in module_name


# --- preferences layout (item 22) -----------------------------------------------------

def test_preferences_draw_separate_update_and_solver_sections(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    import cloth_next.blender.preferences as preferences
    monkeypatch.setattr(preferences, "_safe_read_current", lambda: (None, True))
    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.layout = FakeLayout()
    prefs.external_solver_path = ""
    prefs.update_channel = "BETA"
    prefs.draw(env.bpy.context)
    log = prefs.layout.log
    pairs = [entry for entry in log if len(entry) == 2]
    labels = [text for kind, text in pairs if kind == "label"]
    operators = [text for kind, text in pairs if kind == "operator"]
    assert "Cloth NeXt" in labels
    assert "PPF Contact Solver" in labels
    assert labels.index("Cloth NeXt") < labels.index("PPF Contact Solver")
    assert any(text.startswith("Installed Version:") for text in labels)
    assert any(text.startswith("Update Status:") for text in labels)
    assert ("prop", "update_channel") in log
    assert "clothnext.addon_update_check" in operators
    assert "clothnext.addon_open_release_notes" in operators
    # solver operators are never presented inside the Cloth NeXt update block
    boxes = [i for i, entry in enumerate(log) if entry == ("box",)]
    assert len(boxes) >= 2
    update_block = [entry for entry in log[boxes[0]:boxes[1]] if len(entry) == 2]
    assert all(not (kind == "operator" and value.startswith("clothnext.solver"))
               for kind, value in update_block)
    env.registration.unregister()


def test_release_notes_operator_opens_documented_urls(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    module = updater(env)
    opened = []
    monkeypatch.setattr(module.webbrowser, "open", opened.append)
    op = module.CLOTHNEXT_OT_addon_open_release_notes()
    op.execute(env.bpy.context)
    assert opened == ["https://github.com/polyworks-art/Cloth_neXT/releases"]
    module.session().latest = parse_version("9.9.9-rc.3")
    op.execute(env.bpy.context)
    assert opened[-1].endswith("/releases/tag/v9.9.9-rc.3")
    env.registration.unregister()


# --- registration safety (items 24+25) ------------------------------------------------

def test_partial_registration_failure_rolls_back_update_classes(blender_env, monkeypatch):
    env = blender_env
    module = updater(env)
    original = env.bpy.utils.register_class

    def failing_register(cls):
        if cls is module.CLOTHNEXT_OT_addon_open_release_notes:
            raise RuntimeError("simulated update class failure")
        original(cls)

    monkeypatch.setattr(env.bpy.utils, "register_class", failing_register)
    with pytest.raises(RuntimeError, match="simulated update class failure"):
        env.registration.register()
    assert env.bpy.registry == []
    assert env.bpy.app.timers.functions == []
    assert not hasattr(env.bpy.types.Object, "cloth_next")


def test_repeated_cycles_leave_no_update_state(blender_env):
    env = blender_env
    for _ in range(3):
        env.registration.register()
        env.registration.unregister()
        assert env.bpy.registry == []
        assert env.bpy.app.timers.functions == []
        assert updater(env)._worker is None
        assert updater(env)._owned_managers == []


# --- channel selection at operator level ----------------------------------------------

def test_selected_channel_reads_preferences_and_falls_back(blender_env):
    env = blender_env
    module = updater(env)
    set_channel(env, "STABLE")
    assert module.selected_channel(env.bpy.context) is UpdateChannel.STABLE
    set_channel(env, "BETA")
    assert module.selected_channel(env.bpy.context) is UpdateChannel.BETA
    env.bpy.context.preferences.addons.clear()
    assert module.selected_channel(env.bpy.context) is module.DEFAULT_CHANNEL


def test_dev_selection_never_silently_falls_back_to_beta(blender_env):
    env=blender_env; module=updater(env)
    env.bpy.context.preferences.addons["cloth_next"] = SimpleNamespace(
        preferences=SimpleNamespace(update_channel="DEV",developer_tools=False,
                                    dev_channel_acknowledged=False))
    assert module.selected_channel(env.bpy.context) is UpdateChannel.DEV
    assert "Developer" in module.dev_access_error(env.bpy.context,UpdateChannel.DEV)
    op=module.CLOTHNEXT_OT_addon_update_check()
    assert op.execute(env.bpy.context)=={"CANCELLED"}
    assert module.session().state is AddonUpdateState.INSTALL_BLOCKED
    assert "Beta" not in module.session().message
