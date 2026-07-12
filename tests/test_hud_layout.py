# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from cloth_next.bake.status import BakeJobKind, BakeSnapshot, BakeState
from cloth_next.telemetry.hud_layout import build_hud_card
from cloth_next.telemetry.snapshot import GpuTelemetry, SystemTelemetrySnapshot

def _telemetry():
    return SystemTelemetrySnapshot((GpuTelemetry(1,"RTX",73,6<<30,12<<30,60,200),),25,8<<30,32<<30)

def test_layout_anchors_bounds_and_narrow_fallback():
    snap=BakeSnapshot(state=BakeState.SIMULATING,job_kind=BakeJobKind.SOLVER_TEST,current_frame=5,frame_end=8)
    for anchor in ("TOP_LEFT","TOP_RIGHT","BOTTOM_LEFT","BOTTOM_RIGHT"):
        card=build_hud_card(snap,_telemetry(),anchor=anchor,viewport_width=800,viewport_height=600)
        assert 0<=card.x<=800-card.width and 0<=card.y<=600-card.height
        assert "UI Preview" not in card.title and any("VRAM" in x for x in card.lines)
    assert build_hud_card(snap,_telemetry(),viewport_width=320).mode=="COMPACT"

def test_preview_and_unavailable_are_honest():
    preview=BakeSnapshot(state=BakeState.SIMULATING,job_kind=BakeJobKind.PREVIEW)
    card=build_hud_card(preview,SystemTelemetrySnapshot(),mode="COMPACT")
    assert "UI Preview" in card.title and "Telemetry unavailable" in card.lines

def test_finished_real_run_is_eight_of_eight():
    snap=BakeSnapshot(state=BakeState.FINISHED,job_kind=BakeJobKind.SOLVER_TEST,
                      current_frame=8,frame_end=8,progress_current=8,progress_total=8)
    assert any("Frame 8 / 8" in x for x in build_hud_card(snap,_telemetry()).lines)
