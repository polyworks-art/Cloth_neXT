"""The optional viewport presentation shares the existing UI state."""
from __future__ import annotations

from types import SimpleNamespace
from tests.fake_bpy import _resolved_props


def test_new_look_defaults_off_and_hides_legacy_panel(blender_env, monkeypatch):
    preferences = __import__("cloth_next.blender.preferences", fromlist=["x"])
    prefs = preferences.CLOTHNEXT_AddonPreferences()
    assert _resolved_props(preferences.CLOTHNEXT_AddonPreferences)[
        "new_look"].keywords["default"] is False
    prefs.new_look = False
    context = SimpleNamespace(object=SimpleNamespace(
        cloth_next=SimpleNamespace(role="CLOTH")))
    ui = blender_env.physics_ui
    monkeypatch.setattr(ui.CLOTHNEXT_PT_physics, "poll",
                        classmethod(lambda _cls, _context: True))
    monkeypatch.setattr(ui, "addon_preferences", lambda *_args: prefs)
    assert ui.CLOTHNEXT_PT_simulation.poll(context)
    prefs.new_look = True
    assert not ui.CLOTHNEXT_PT_simulation.poll(context)


def test_handler_tracks_preference_without_duplicates(blender_env, monkeypatch):
    floating = __import__("cloth_next.blender.floating_simulation",
                          fromlist=["sync"])
    prefs = SimpleNamespace(new_look=False)
    context = SimpleNamespace(window_manager=SimpleNamespace(windows=()))
    monkeypatch.setattr(floating, "_prefs", lambda _context: prefs)
    added, removed = [], []
    monkeypatch.setattr(blender_env.bpy.types.SpaceView3D,
                        "draw_handler_add", lambda *_args: added.append(1) or 1)
    monkeypatch.setattr(blender_env.bpy.types.SpaceView3D,
                        "draw_handler_remove", lambda *_args: removed.append(1))
    floating.register()
    assert added == []
    prefs.new_look = True
    floating.sync(context)
    floating.sync(context)
    assert added == [1]
    prefs.new_look = False
    floating.sync(context)
    assert removed == [1]
    floating.unregister()
