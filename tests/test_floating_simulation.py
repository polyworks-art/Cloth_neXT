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
    assert floating.visible(context)  # slides out before becoming non-interactive
    floating._slide_fraction(floating._animation_start_time + .2)
    assert not floating.visible(context)
    assert prefs.new_look
    assert toggle.execute(context) == {"FINISHED"}
    floating._slide_fraction(floating._animation_start_time + .2)
    assert floating.visible(context)
    toggle.execute(context)
    prefs.new_look = False
    floating.sync(context)
    assert toggle.execute(context) == {"CANCELLED"}
    prefs.new_look = True
    floating.sync(context)
    assert floating.visible(context)
    floating.unregister()


def test_f6_slide_reverses_and_timer_stops(blender_env, monkeypatch):
    floating = __import__("cloth_next.blender.floating_simulation",
                          fromlist=["sync"])
    prefs = SimpleNamespace(new_look=True)
    context = SimpleNamespace(window_manager=SimpleNamespace(windows=()))
    monkeypatch.setattr(floating, "_prefs", lambda _context: prefs)
    monkeypatch.setattr(floating, "_tag_redraw", lambda _context: None)
    clock = [100.0]
    monkeypatch.setattr(floating.time, "monotonic", lambda: clock[0])
    floating.register()
    toggle = floating.CLOTHNEXT_OT_toggle_floating_ui()
    timers = blender_env.bpy.app.timers
    assert toggle.execute(context) == {"FINISHED"}
    assert timers.is_registered(floating._animation_tick)
    clock[0] += .06
    assert 0 < floating._slide_fraction() < 1
    assert toggle.execute(context) == {"FINISHED"}
    assert 0 < floating._animation_from < 1
    floating._slide_fraction(floating._animation_start_time + .2)
    assert floating.visible(context)
    assert floating._slide == 1
    assert toggle.execute(context) == {"FINISHED"}
    floating._slide_fraction(floating._animation_start_time + .2)
    assert floating._slide == 0
    assert not floating.visible(context)
    assert not floating.CLOTHNEXT_GT_floating_simulation.poll(context)
    assert floating._animation_tick() is None
    floating.unregister()
    assert not timers.is_registered(floating._animation_tick)


def test_slide_uses_each_viewport_region_bounds(blender_env, monkeypatch):
    floating = __import__("cloth_next.blender.floating_simulation",
                          fromlist=["_animated_bounds"])
    monkeypatch.setattr(floating, "_quality_width", lambda _context: 66)
    context = SimpleNamespace(
        preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1)),
        region=SimpleNamespace(type="WINDOW", width=600, height=400))
    floating._stop_animation()
    floating._slide = 1.0
    shown = floating._animated_bounds(context)
    context.region.width = 900
    wider = floating._animated_bounds(context)
    assert wider[0] - shown[0] == 150
    floating._slide = 0.0
    hidden = floating._animated_bounds(context)
    assert hidden[1] + hidden[3] + 13 + 7 < 0
    quick = floating.quick_assign.button_bounds(context)
    assert quick[1] + quick[2] < 0
    floating._slide = 1.0


def test_scene_load_clears_stale_image_references(blender_env):
    floating = __import__("cloth_next.blender.floating_simulation",
                          fromlist=["_scene_loaded"])
    floating._images["cloth_next"] = object()
    floating._scene_loaded(None)
    assert floating._images == {}


def test_diagnostics_are_outside_pill_at_bake_height(blender_env):
    floating = __import__("cloth_next.blender.floating_simulation", fromlist=["x"])
    x, y = floating._message_anchor((100, 20, 200, 54*.65, .65))
    assert x-12 > 300  # clickable icon also stays outside the pill
    assert y == 20+27*.65


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
