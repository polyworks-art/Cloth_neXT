# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
from pathlib import Path
import inspect
import json
import re
import tomllib

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

def test_release_versions_remain_consistent_beta_or_rc():
    package=Path(__file__).parents[1]/"cloth_next"
    manifest=tomllib.loads((package/"blender_manifest.toml").read_text("utf-8"))
    compatibility=json.loads((package/"solver_compatibility.json").read_text("utf-8"))
    version=manifest["version"]
    assert re.fullmatch(
        r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)-(?:dev|beta|rc)\.[1-9]\d*",
        version)
    assert compatibility["cloth_next_version"] == version
