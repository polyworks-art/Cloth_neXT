# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
from pathlib import Path
import inspect
import json
import tomllib
from types import SimpleNamespace

def test_every_physics_panel_requests_expected_custom_icon(blender_env, monkeypatch):
    ui=blender_env.physics_ui; requested=[]
    monkeypatch.setattr(ui.icon_registry,"icon_kwargs",
                        lambda name,fallback="NONE": requested.append(name) or {"icon":fallback})
    class Layout:
        def label(self,**_kwargs): pass
    expected={ui.CLOTHNEXT_PT_physics:"cloth_next",ui.CLOTHNEXT_PT_overview:"cloth",
        ui.CLOTHNEXT_PT_solver:"solver",
        ui.CLOTHNEXT_PT_material:"physical",ui.CLOTHNEXT_PT_damping:"damping",
        ui.CLOTHNEXT_PT_collisions:"collision",ui.CLOTHNEXT_PT_cache:"cache",
        ui.CLOTHNEXT_PT_advanced:"advanced"}
    for panel,icon in expected.items():
        instance=panel(); instance.layout=Layout(); instance.draw_header(None)
        assert requested[-1]==icon

def test_hud_draw_source_has_no_hardware_or_process_calls(blender_env):
    source=inspect.getsource(__import__("cloth_next.blender.hud",fromlist=["x"])._draw)
    for forbidden in ("subprocess", "nvidia-smi", "query_nvidia", "Popen(", "open("):
        assert forbidden not in source


def test_hud_redraw_timer_updates_idle_viewport_without_mouse_input(
        blender_env, monkeypatch):
    hud=__import__("cloth_next.blender.hud",fromlist=["x"])
    redraws=[]
    area=SimpleNamespace(type="VIEW_3D",tag_redraw=lambda:redraws.append(True))
    blender_env.bpy.context.window_manager=SimpleNamespace(windows=(
        SimpleNamespace(screen=SimpleNamespace(areas=(area,))),))
    monkeypatch.setattr(hud,"_preferences",lambda:SimpleNamespace(
        show_bake_hud=True,telemetry_refresh_seconds=.5))
    monkeypatch.setattr(hud.shared_controller,"snapshot",lambda:
        SimpleNamespace(state=hud.BakeState.PREPARING))

    assert hud._redraw_pulse() == .5
    assert redraws == [True]

def test_release_versions_remain_consistent_and_channel_encoded():
    package=Path(__file__).parents[1]/"cloth_next"
    manifest=tomllib.loads((package/"blender_manifest.toml").read_text("utf-8"))
    compatibility=json.loads((package/"solver_compatibility.json").read_text("utf-8"))
    version=manifest["version"]
    from cloth_next.updater.addon_versions import parse_version
    assert parse_version(version).channel_name in {"dev", "beta", "stable"}
    assert compatibility["cloth_next_version"] == version
