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

    def split(self, factor=0.0, align=False):
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


def test_solver_section_hides_retired_selected_release(blender_env, monkeypatch,
                                                       tmp_path):
    old = make_installation(
        tmp_path,
        installation_id="old",
        display_name="Simulation Solver 2026-07-13",
        protocol="0.11",
        schema="1",
        release_tag="old-tag",
    )
    current = make_installation(
        tmp_path,
        installation_id="current",
        display_name="Simulation Solver 2026-07-26",
        protocol="0.13",
        schema="2",
        release_tag="current-tag",
    )
    registry = SolverRegistry((old, current), old.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)
    requested_icons = []

    def record_icon(name, fallback="NONE"):
        requested_icons.append((name, fallback))
        return {"icon": fallback}

    monkeypatch.setattr(compact_ui.icon_registry, "icon_kwargs", record_icon)

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = old.installation_id
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    labels = [text for text, _icon in layout.labels]
    assert "Solver Engine" in labels
    assert "Solver" not in labels
    assert ("gaia_engine", "NONE") in requested_icons
    assert old.display_name not in labels
    assert current.display_name not in labels
    assert "No Solver Selected" in labels
    assert "Simulation Solver" not in labels
    assert "Solver Installations" not in labels
    assert "Installed" not in labels
    assert "Available Downloads" not in labels
    assert layout.props == [
        ("solver_engine", "Engine", True),
        ("selected_solver_installation_id", "Release", True),
    ]
    assert layout.menus == [
        ("CLOTHNEXT_MT_solver_manage", "Manage", True)
    ]
    assert not any(idname == "clothnext.solver_health_check"
                   for idname, _text, _operator, _enabled in layout.operators)

    compact_ui.uninstall()


def test_single_installed_release_hides_release_selector(blender_env, monkeypatch,
                                                         tmp_path):
    active = make_installation(
        tmp_path,
        installation_id="active",
        display_name="Solver Release",
        protocol="0.13",
        schema="2",
        release_tag="active-tag",
    )
    registry = SolverRegistry((active,), active.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = active.installation_id
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    assert layout.props == [("solver_engine", "Engine", True)]
    assert layout.menus == [
        ("CLOTHNEXT_MT_solver_manage", "Manage", True)
    ]

    compact_ui.uninstall()


def test_registered_release_without_selection_shows_selector_not_install(
        blender_env, monkeypatch, tmp_path):
    installed = make_installation(
        tmp_path,
        installation_id="available",
        display_name="Available Release",
        protocol="0.13",
        schema="2",
        release_tag="available-tag",
    )
    registry = SolverRegistry((installed,), None)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = "NONE"
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    labels = [text for text, _icon in layout.labels]
    assert "No Solver Selected" in labels
    assert "The selected solver installation is missing." not in labels
    assert layout.props == [
        ("solver_engine", "Engine", True),
        ("selected_solver_installation_id", "Release", True),
    ]
    assert not any(
        idname == "clothnext.solver_download" and text == "Install Solver"
        for idname, text, _operator, _enabled in layout.operators
    )

    compact_ui.uninstall()


def test_gaia_backend_switch_and_rocm_warning(blender_env, monkeypatch, tmp_path):
    gaia = make_installation(
        tmp_path, installation_id="gaia", display_name="Gaia",
        protocol="0.22", schema="2", release_tag="2026-09-21-21-32")
    for backend in ("cuda", "rocm", "cpu"):
        release = gaia.root / "target" / backend / "release"
        release.mkdir(parents=True)
        (release / "ppf-cts-server.exe").touch()
    registry = SolverRegistry((gaia,), gaia.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)
    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = gaia.installation_id
    prefs.solver_backend_choice = "ROCM"
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)
    assert ("solver_backend_choice", "Backend", True) in layout.props
    assert any("general AMD GPU support is not yet verified" in text
               for text, _icon in layout.labels)
    compact_ui.uninstall()


def test_backend_switch_is_disabled_during_bake(blender_env, monkeypatch,
                                                tmp_path):
    gaia = make_installation(
        tmp_path, installation_id="gaia", display_name="Gaia",
        protocol="0.22", schema="2", release_tag="2026-09-21-21-32")
    registry = SolverRegistry((gaia,), gaia.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)
    monkeypatch.setattr(preferences, "_solver_session_active", lambda: True)
    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = gaia.installation_id
    prefs.solver_backend_choice = "AUTO"
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)
    assert ("solver_backend_choice", "Backend", False) in layout.props
    compact_ui.uninstall()


def test_manage_menu_offers_downloadable_generations(blender_env, monkeypatch,
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
            release_id="ppf-0.13-stable",
            display_name="Velune",
            official_release_tag="velune-tag",
            downloadable=False,
        ),
        SimpleNamespace(
            release_id="ppf-0.18-current",
            display_name="Lumen",
            official_release_tag="lumen-tag",
            downloadable=True,
        ),
        SimpleNamespace(
            release_id="ppf-0.22-gaia",
            display_name="Gaia",
            official_release_tag="gaia-tag",
            downloadable=True,
        ),
    )
    preferences._session.entries = entries
    preferences._session.entry = entries[2]

    menu = compact_ui.CLOTHNEXT_MT_solver_manage()
    menu.layout = RecordingLayout()
    menu.draw(None)

    release_actions = {
        operator.release_id: (text, operator)
        for idname, text, operator, _enabled in menu.layout.operators
        if idname == "clothnext.solver_download"
    }
    assert set(release_actions) == {"ppf-0.18-current", "ppf-0.22-gaia"}
    lumen_text, lumen = release_actions["ppf-0.18-current"]
    assert lumen_text == "Install AETHER lumen-tag"
    assert lumen.activate_after_install is False
    assert release_actions["ppf-0.22-gaia"][1].activate_after_install is True

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


def test_learning_section_links_to_learning_center(blender_env):
    import cloth_next.blender.preferences as preferences

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    layout = RecordingLayout()
    prefs._draw_learning_section(layout)

    links = [
        (text, operator.url)
        for idname, text, operator, _enabled in layout.operators
        if idname == "wm.url_open"
    ]
    assert links == [("Learning Center", preferences.LEARNING_CENTER_URL)]
    assert preferences.LEARNING_CENTER_URL == (
        "https://polyworks-art.github.io/Cloth_neXT/docs/")


def test_dynamic_enum_items_stay_referenced_after_the_callback(blender_env):
    """Blender keeps only pointers to item strings; a fresh list garbles the menu."""
    from cloth_next.blender import preferences
    first = preferences._solver_enum_items(None, None)
    assert first is preferences._SOLVER_ITEMS
    assert preferences._engine_items(None, None) is preferences._ENGINE_ITEMS
    assert all(isinstance(item[1], str) and item[1] for item in first)


class CollapsedPanelLayout(RecordingLayout):
    """Like Blender: ``panel`` returns no body while the sub-panel is closed."""

    def box(self):
        return CollapsedPanelLayout(self.root)

    def panel(self, idname, default_closed=False):
        self.root.panels = getattr(self.root, "panels", []) + [(idname, default_closed)]
        return RecordingLayout(self.root), None


def test_aether_row_offers_update_without_switching_selection(
        blender_env, monkeypatch, tmp_path):
    active = make_installation(
        tmp_path, installation_id="active", display_name="Solver Release",
        protocol="0.13", schema="2", release_tag="old-tag")
    registry = SolverRegistry((active,), active.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)
    newer = SimpleNamespace(release_id="ppf-new", official_release_tag="new-tag",
                            downloadable=True)
    preferences._session.entries = (newer,)
    preferences._session.entry = newer

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = active.installation_id
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    labels = [text for text, _icon in layout.labels]
    assert "AETHER" in labels and "GAIA" in labels
    assert "old-tag installed" in labels
    updates = [(text, operator) for idname, text, operator, _enabled in layout.operators
               if idname == "clothnext.solver_download"]
    assert [text for text, _operator in updates] == ["Update AETHER"]
    assert updates[0][1].release_id == "ppf-new"
    assert updates[0][1].activate_after_install is False

    compact_ui.uninstall()


def test_aether_row_has_no_action_when_current_release_installed(
        blender_env, monkeypatch, tmp_path):
    active = make_installation(
        tmp_path, installation_id="active", display_name="Solver Release",
        protocol="0.13", schema="2", release_tag="current-tag")
    registry = SolverRegistry((active,), active.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)
    current = SimpleNamespace(release_id="ppf-current", official_release_tag="current-tag",
                              downloadable=True)
    preferences._session.entries = (current,)
    preferences._session.entry = current

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = active.installation_id
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    assert not any(idname == "clothnext.solver_download"
                   for idname, _text, _operator, _enabled in layout.operators)
    compact_ui.uninstall()


def test_technical_details_are_collapsed_by_default(blender_env, monkeypatch, tmp_path):
    first = make_installation(
        tmp_path, installation_id="first", display_name="First Release",
        protocol="0.13", schema="2", release_tag="first-tag")
    second = make_installation(
        tmp_path, installation_id="second", display_name="Second Release",
        protocol="0.13", schema="2", release_tag="second-tag")
    registry = SolverRegistry((first, second), first.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = first.installation_id
    layout = CollapsedPanelLayout()
    prefs._draw_solver_section(layout)

    labels = [text for text, _icon in layout.labels]
    assert layout.panels == [("clothnext_solver_details", True)]
    assert "Details" in labels
    assert not any(text.startswith("Protocol ") for text in labels)
    assert first.display_name not in labels
    assert ("selected_solver_installation_id", "Release", True) not in layout.props
    compact_ui.uninstall()


def test_reproducible_bake_shares_the_backend_row(
        blender_env, monkeypatch, tmp_path):
    gaia = make_installation(
        tmp_path, installation_id="gaia", display_name="GAIA build",
        protocol="0.26", schema="2", release_tag=None)
    release = gaia.root / "target" / "cuda" / "release"
    release.mkdir(parents=True)
    (release / "ppf-cts-server.exe").touch()
    registry = SolverRegistry((gaia,), gaia.installation_id)
    preferences, compact_ui = install_compact_ui(monkeypatch, registry)
    from cloth_next.updater import engines
    monkeypatch.setattr(engines, "engine_of",
                        lambda item: engines.GAIA if item is gaia else engines.AETHER)

    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.selected_solver_installation_id = gaia.installation_id
    prefs.gaia_reproducible_bake = False
    layout = RecordingLayout()
    prefs._draw_solver_section(layout)

    assert layout.props[:3] == [
        ("solver_engine", "Engine", True),
        ("solver_backend_choice", "Backend", True),
        ("gaia_reproducible_bake", None, True),
    ]
    assert not any(text.startswith("Slower.") for text, _icon in layout.labels)
    compact_ui.uninstall()
