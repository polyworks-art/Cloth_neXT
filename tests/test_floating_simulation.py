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


def test_visibility_toggle_is_runtime_only_and_resets_on_reenable(
        blender_env, monkeypatch):
    floating = __import__("cloth_next.blender.floating_simulation",
                          fromlist=["sync"])
    prefs = SimpleNamespace(new_look=False)
    context = SimpleNamespace(window_manager=SimpleNamespace(windows=()))
    monkeypatch.setattr(floating, "_prefs", lambda _context: prefs)
    monkeypatch.setattr(floating, "_tag_redraw", lambda _context: None)
    floating.register()
    prefs.new_look = True
    floating.sync(context)
    toggle = floating.CLOTHNEXT_OT_toggle_floating_ui()
    assert floating.visible(context)
    assert toggle.execute(context) == {"FINISHED"}
    assert not floating.visible(context)
    assert prefs.new_look
    assert toggle.execute(context) == {"FINISHED"}
    assert floating.visible(context)
    toggle.execute(context)
    prefs.new_look = False
    floating.sync(context)
    assert toggle.execute(context) == {"CANCELLED"}
    prefs.new_look = True
    floating.sync(context)
    assert floating.visible(context)
    floating.unregister()


def test_scene_load_clears_stale_image_references(blender_env):
    floating = __import__("cloth_next.blender.floating_simulation",
                          fromlist=["_scene_loaded"])
    floating._images["cloth_next"] = object()
    floating._scene_loaded(None)
    assert floating._images == {}


def test_f6_keymap_registers_once_and_cleans_up(blender_env, monkeypatch):
    floating = __import__("cloth_next.blender.floating_simulation",
                          fromlist=["register"])

    class Items(list):
        def new(self, idname, key, value):
            item = SimpleNamespace(idname=idname, type=key, value=value)
            self.append(item)
            return item

    class Keymaps:
        def __init__(self):
            self.km = SimpleNamespace(keymap_items=Items())

        def new(self, *, name, space_type):
            assert (name, space_type) == ("3D View", "VIEW_3D")
            return self.km

    keymaps = Keymaps()
    wm = SimpleNamespace(
        windows=(), keyconfigs=SimpleNamespace(
            addon=SimpleNamespace(keymaps=keymaps)))
    monkeypatch.setattr(blender_env.bpy.context, "window_manager", wm)
    floating.register()
    floating.register()
    assert len(keymaps.km.keymap_items) == 1
    assert keymaps.km.keymap_items[0].type == "F6"
    floating.unregister()
    assert keymaps.km.keymap_items == []
    floating.register()
    assert len(keymaps.km.keymap_items) == 1
    floating.unregister()
