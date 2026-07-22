# SPDX-License-Identifier: GPL-3.0-or-later
"""Developer UI is one Dev-build- and preference-gated Cache child panel."""

from __future__ import annotations

from types import SimpleNamespace

from tests import fake_bpy


class RecordingLayout:
    def __init__(self, root=None):
        self.root = root or self
        if root is None:
            self.labels = []
            self.operators = []
            self.boxes = []
            self.separators = 0
        self.enabled = True
        self.alert = False

    def label(self, text="", **_kw):
        self.root.labels.append(text)

    def operator(self, identifier, **_kw):
        self.root.operators.append(identifier)
        return SimpleNamespace()

    def prop(self, *_args, **_kw):
        pass

    def row(self, **_kw):
        return RecordingLayout(self.root)

    def column(self, **_kw):
        return RecordingLayout(self.root)

    def box(self):
        box = RecordingLayout(self.root)
        self.root.boxes.append(box)
        return box

    def separator(self):
        self.root.separators += 1


def context_for(env, *, developer_tools=False, with_preferences=True,
                role="CLOTH"):
    obj = env.bpy.types.Object(name="Cloth", type="MESH")
    obj.cloth_next.enabled = True
    obj.cloth_next.role = role
    addons = ({"cloth_next": SimpleNamespace(preferences=SimpleNamespace(
        developer_tools=developer_tools))} if with_preferences else {})
    return SimpleNamespace(object=obj, active_object=obj,
                           preferences=SimpleNamespace(addons=addons))


def test_existing_preference_is_authoritative_and_artist_facing(blender_env):
    prop = fake_bpy._resolved_props(
        blender_env.registration.preferences.CLOTHNEXT_AddonPreferences)["developer_tools"]
    assert prop.keywords["default"] is False
    assert prop.keywords["name"] == "Developer Tools"
    description = prop.keywords["description"].lower()
    assert "solver tests" in description and "ui diagnostics" in description


def test_panel_hierarchy_defaults_and_registration_order(blender_env):
    ui = blender_env.physics_ui
    panel = ui.CLOTHNEXT_PT_developer_tools
    assert panel.bl_parent_id == "CLOTHNEXT_PT_cache"
    assert panel.bl_options == {"DEFAULT_CLOSED"}
    assert panel.deformable_only is True
    assert ui.CLASSES.index(ui.CLOTHNEXT_PT_cache) < ui.CLASSES.index(panel)


def test_panel_poll_requires_dev_build_preference_and_cloth(blender_env,
                                                             monkeypatch):
    env = blender_env
    env.registration.register()
    panel = env.physics_ui.CLOTHNEXT_PT_developer_tools
    context = context_for(env, developer_tools=True)
    monkeypatch.setattr(env.physics_ui, "_developer_tools_build_enabled", lambda: False)
    assert panel.poll(context) is False
    monkeypatch.setattr(env.physics_ui, "_developer_tools_build_enabled", lambda: True)
    assert panel.poll(context) is True
    assert panel.poll(context_for(env, developer_tools=False)) is False
    assert panel.poll(context_for(env, developer_tools=True,
                                  with_preferences=False)) is False
    assert panel.poll(context_for(env, developer_tools=True,
                                  role="COLLIDER")) is False
    env.registration.unregister()


def test_one_shared_alert_box_contains_both_sections(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    monkeypatch.setattr(env.physics_ui, "_developer_tools_build_enabled", lambda: True)
    monkeypatch.setattr(env.solver_test, "run_active", lambda: False)
    panel = env.physics_ui.CLOTHNEXT_PT_developer_tools()
    panel.layout = RecordingLayout()
    panel.draw(context_for(env, developer_tools=True))
    assert len(panel.layout.boxes) == 1
    assert panel.layout.boxes[0].alert is True
    assert "Real Solver Test" in panel.layout.labels
    assert "UI Diagnostics" in panel.layout.labels
    assert panel.layout.separators == 1
    assert "clothnext.preview_start" in panel.layout.operators
    assert "clothnext.preview_cancel" not in panel.layout.operators
    env.registration.unregister()


def test_preview_cancel_only_during_active_preview(blender_env, monkeypatch):
    env = blender_env
    snapshot = SimpleNamespace(preview=True, active=True)
    monkeypatch.setattr(env.physics_ui.shared_controller, "snapshot", lambda: snapshot)
    layout = RecordingLayout()
    env.physics_ui._draw_ui_diagnostics_controls(layout, None)
    assert layout.operators == ["clothnext.preview_start", "clothnext.preview_cancel"]


def test_cache_draw_has_no_developer_controls(blender_env):
    env = blender_env
    env.registration.register()
    panel = env.physics_ui.CLOTHNEXT_PT_cache()
    panel.layout = RecordingLayout()
    panel.draw(context_for(env, developer_tools=True))
    forbidden = {"clothnext.create_test_scene", "clothnext.solver_test_run",
                 "clothnext.preview_start", "clothnext.preview_cancel"}
    assert not forbidden.intersection(panel.layout.operators)
    env.registration.unregister()


def test_registration_cycles_do_not_duplicate_developer_panel(blender_env):
    env = blender_env
    for _ in range(2):
        env.registration.register()
        assert env.bpy.registry.count(env.physics_ui.CLOTHNEXT_PT_developer_tools) == 1
        env.registration.unregister()
        assert env.physics_ui.CLOTHNEXT_PT_developer_tools not in env.bpy.registry
