# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
from pathlib import Path
import json
import tomllib
from types import SimpleNamespace

def test_every_physics_panel_requests_expected_custom_icon(blender_env, monkeypatch):
    ui=blender_env.physics_ui; requested=[]
    monkeypatch.setattr(ui.icon_registry,"icon_kwargs",
                        lambda name,fallback="NONE": requested.append(name) or {"icon":fallback})
    class Layout:
        def label(self,**_kwargs): pass
    expected={ui.CLOTHNEXT_PT_physics:"cloth_next",ui.CLOTHNEXT_PT_solver:"solver",
        ui.CLOTHNEXT_PT_material:"physical",ui.CLOTHNEXT_PT_damping:"damping",
        ui.CLOTHNEXT_PT_collisions:"collision",ui.CLOTHNEXT_PT_cache:"cache",
        ui.CLOTHNEXT_PT_advanced:"advanced"}
    for panel,icon in expected.items():
        instance=panel(); instance.layout=Layout(); instance.draw_header(None)
        assert requested[-1]==icon

def test_resource_monitor_is_not_registered(blender_env):
    assert not any(cls.__name__.startswith("CLOTHNEXT_GT_resource")
                   for cls in blender_env.registration._CLASSES)
    assert "hud" not in blender_env.registration.__dict__


def test_telemetry_still_tracks_bake_for_memory_safety(blender_env, monkeypatch):
    runtime = __import__("cloth_next.blender.telemetry_runtime", fromlist=["x"])
    enabled = []
    monkeypatch.setattr(runtime.shared_telemetry, "set_enabled", enabled.append)
    runtime._on_snapshot(SimpleNamespace(active=True))
    runtime._on_snapshot(SimpleNamespace(active=False))
    assert enabled == [True, False]

def test_release_versions_remain_consistent_and_channel_encoded():
    package=Path(__file__).parents[1]/"cloth_next"
    manifest=tomllib.loads((package/"blender_manifest.toml").read_text("utf-8"))
    compatibility=json.loads((package/"solver_compatibility.json").read_text("utf-8"))
    version=manifest["version"]
    from cloth_next.updater.addon_versions import parse_version
    assert parse_version(version).channel_name in {"dev", "beta", "stable"}
    assert compatibility["cloth_next_version"] == version
