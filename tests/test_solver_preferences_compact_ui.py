# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fake-bpy coverage for the compact solver preferences surface."""

from __future__ import annotations

from types import SimpleNamespace

from cloth_next.updater.solver_registry import SolverInstallation, SolverRegistry


class RecordingLayout:
    def __init__(self, root=None):
        self.root = root or self
        self.enabled = True
        if root is None:
            self.labels = []
            self.operators = []
            self.menus = []
            self.props = []
            self.separators = 0

    def box(self):
        return RecordingLayout(self.root)

    def row(self, align=False):
        return RecordingLayout(self.root)

    def column(self, align=False):
        return RecordingLayout(self.root)

    def label(self, text="", icon=None):
        self.root.labels.append((text, icon))

    def operator(self, idname, text=None, icon=None):
        operator = SimpleNamespace()
        self.root.operators.append((idname, text, operator, self.enabled))
        return operator

    def menu(self, idname, text=None, icon=None):
        self.root.menus.append((idname, text, self.enabled))

    def prop(self, _data, prop_name, text=None):
        self.root.props.append((prop_name, text, self.enabled))

    def separator(self):
        self.root.separators += 1


def make_installation(tmp_path, *, installation_id, display_name,
                      protocol, schema, release_tag):
    root = tmp_path / installation_id
    root.mkdir()
    executable = root / "ppf-cts-server.exe"
    executable.write_bytes(b"solver")
    return SolverInstallation(
        installation_id=installation_id,
        display_name=display_name,
        source="official",
        root_path=str(root),
        executable_path=str(executable),
        frontend_path=str(root / "frontend"),
        package_version="0.1.0",
        protocol_version=protocol,
        schema_version=schema,
        official_release_tag=release_tag,
        managed=True,
        verified=True,
        healthy=True,
        channel="stable",
    )


def install_compact_ui(monkeypatch, registry):
    import cloth_next.blender.preferences as preferences
    import cloth_next.blender.solver_preferences_ui as compact_ui

    monkeypatch.setattr(preferences, "_read_registry", lambda: (registry, None))
    monkeypatch.setattr(preferences, "_solver_session_active", lambda: False)
    monkeypatch.setattr(preferences._session, "load", lambda: None)
    compact_ui.install()
    return preferences, compact_ui


def test_solver_section_only_shows_active_release(blender_env, monkeypatch,
                                                   tmp_path):
    old = make_installation(
        tmp_path,
        installation_id="old",
        display_name="PPF Contact Solver 2026-07-13",
        protocol="0.11",
        schema="1",
        release_tag="old-tag",
    )
    current = make_installation(
        tmp_path,
        installation_id="current",
        display_name="PPF Contact Solver 2026-07-26",
        protocol="0.13",
        schema="2",
        release_tag="current-tag",
    )
    registry = SolverRegistry((old, current), old.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = old.installation_id
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    labels = [text for text, _icon in layout.labels]
    assert "Solver" in labels
    assert old.display_name in labels
    assert current.display_name not in labels
    assert "Ready" in labels
    assert "Protocol 0.11 · Schema 1 · Managed" in labels
    assert "PPF Contact Solver" not in labels
    assert "Solver Installations" not in labels
    assert "Installed" not in labels
    assert "Available Downloads" not in labels
    assert layout.props == [
        ("selected_solver_installation_id", "Release", True)
    ]
    assert layout.menus == [
        ("CLOTHNEXT_MT_solver_manage", "Manage", True)
    ]
    assert any(
        idname == "clothnext.solver_health_check" and text == "Test"
        for idname, text, _operator, _enabled in layout.operators
    )

    compact_ui.uninstall()


def test_single_installed_release_hides_release_selector(blender_env, monkeypatch,
                                                         tmp_path):
    active = make_installation(
        tmp_path,
        installation_id="active",
        display_name="Solver Release",
        protocol="0.11",
        schema="1",
        release_tag="active-tag",
    )
    registry = SolverRegistry((active,), active.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = active.installation_id
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    assert layout.props == []
    assert layout.menus == [
        ("CLOTHNEXT_MT_solver_manage", "Manage", True)
    ]

    compact_ui.uninstall()


def test_manage_menu_keeps_both_transition_releases(blender_env, monkeypatch,
                                                     tmp_path):
    installed = make_installation(
        tmp_path,
        installation_id="old",
        display_name="Legacy Release",
        protocol="0.11",
        schema="1",
        release_tag="old-tag",
    )
    registry = SolverRegistry((installed,), installed.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)
    entries = (
        SimpleNamespace(
            release_id="ppf-0.11-stable",
            display_name="Legacy Release",
            official_release_tag="old-tag",
        ),
        SimpleNamespace(
            release_id="ppf-0.13-current",
            display_name="Current Release",
            official_release_tag="current-tag",
        ),
    )
    preferences._session.entries = entries

    menu = compact_ui.CLOTHNEXT_MT_solver_manage()
    menu.layout = RecordingLayout()
    menu.draw(None)

    release_actions = {
        operator.release_id: (text, operator)
        for idname, text, operator, _enabled in menu.layout.operators
        if idname == "clothnext.solver_download"
    }
    reinstall_text, reinstall = release_actions["ppf-0.11-stable"]
    install_text, install = release_actions["ppf-0.13-current"]
    assert reinstall_text == "Reinstall Legacy Release"
    assert reinstall.reinstall is True
    assert reinstall.activate_after_install is False
    assert install_text == "Install Current Release"
    assert install.activate_after_install is False
    assert not hasattr(install, "reinstall")

    compact_ui.uninstall()


def test_registration_installs_and_restores_compact_renderer(blender_env):
    import cloth_next.blender.preferences as preferences
    import cloth_next.blender.solver_preferences_ui as compact_ui

    original = preferences.CLOTHNEXT_AddonPreferences._draw_solver_section
    blender_env.registration.register()
    assert (
        preferences.CLOTHNEXT_AddonPreferences._draw_solver_section
        is compact_ui.draw_solver_section
    )
    assert compact_ui.CLOTHNEXT_MT_solver_manage in blender_env.bpy.registry

    blender_env.registration.unregister()
    assert preferences.CLOTHNEXT_AddonPreferences._draw_solver_section is original
