# SPDX-License-Identifier: GPL-3.0-or-later
"""Wind's frame handler must stay inert on Blender's render thread."""
import importlib
import threading


def _wind(blender_env):
    return importlib.import_module("cloth_next.blender.wind_fields")


def test_frame_handler_skips_gpu_and_redraw_on_render_thread(blender_env, monkeypatch):
    wind = _wind(blender_env)
    calls = []
    monkeypatch.setattr(wind, "redraw", lambda *a, **k: calls.append("redraw"))
    monkeypatch.setattr(wind, "_invalidate_obstacles",
                        lambda **k: calls.append("invalidate"))
    worker = threading.Thread(target=wind._frame_change_post, args=(None,))
    worker.start(); worker.join()
    assert calls == []


def test_frame_handler_still_refreshes_on_main_thread(blender_env, monkeypatch):
    wind = _wind(blender_env)
    calls = []
    monkeypatch.setattr(wind, "redraw", lambda *a, **k: calls.append("redraw"))
    monkeypatch.setattr(wind, "_invalidate_obstacles",
                        lambda **k: calls.append("invalidate"))
    wind._frame_change_post(None)
    assert calls == ["invalidate", "redraw"]
