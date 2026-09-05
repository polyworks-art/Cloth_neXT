# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace


def test_register_is_one_shot_and_unregister_removes_timer(blender_env):
    manager = __import__("cloth_next.blender.onboarding_manager", fromlist=["x"])
    manager.register()
    manager.register()
    assert blender_env.bpy.app.timers.functions.count(manager._startup_pulse) == 1
    manager.unregister()
    assert manager._startup_pulse not in blender_env.bpy.app.timers.functions


def test_automatic_launch_marks_seen_only_after_window_acknowledges(blender_env,
                                                                monkeypatch):
    manager = __import__("cloth_next.blender.onboarding_manager", fromlist=["x"])
    preferences = SimpleNamespace(onboarding_state="")
    blender_env.bpy.context.preferences.addons[manager._ADDON_ID if hasattr(manager, "_ADDON_ID") else "cloth_next"] = SimpleNamespace(preferences=preferences)
    monkeypatch.setattr(manager, "_preferences", lambda: preferences)
    monkeypatch.setattr(manager, "companion_info_command", lambda *_a, **_k: ["companion"])
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *_a, **_k:
                        SimpleNamespace(poll=lambda: None))
    ok, _message = manager.launch_screen("welcome")
    assert ok
    assert manager._state(preferences).next_screen("2.3.7") == "welcome"
    pending = manager._pending[-1]
    pending[2].write_text(pending[3], encoding="utf-8")
    assert manager._poll_startup() is None
    assert manager._state(preferences).next_screen("2.3.5") is None


def test_failed_launch_does_not_mark_seen(blender_env, monkeypatch):
    manager = __import__("cloth_next.blender.onboarding_manager", fromlist=["x"])
    preferences = SimpleNamespace(onboarding_state="")
    monkeypatch.setattr(manager, "_preferences", lambda: preferences)
    monkeypatch.setattr(manager, "companion_info_command", lambda *_a, **_k: ["missing"])
    monkeypatch.setattr(manager.subprocess, "Popen",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("missing")))
    assert not manager.launch_screen("welcome")[0]
    assert preferences.onboarding_state == ""


def test_manual_open_does_not_change_seen_state(blender_env, monkeypatch):
    manager = __import__("cloth_next.blender.onboarding_manager", fromlist=["x"])
    preferences = SimpleNamespace(onboarding_state="")
    monkeypatch.setattr(manager, "_preferences", lambda: preferences)
    monkeypatch.setattr(manager, "companion_info_command", lambda *_a, **_k: ["companion"])
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *_a, **_k:
                        SimpleNamespace(poll=lambda: None))
    assert manager.launch_screen("welcome", manual=True)[0]
    pending = manager._pending[-1]
    pending[2].write_text(pending[3], encoding="utf-8")
    assert manager._poll_startup() is None
    assert preferences.onboarding_state == ""
