# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
from dataclasses import dataclass
from ..bake.status import BakeJobKind, BakeSnapshot, format_duration
from .snapshot import SystemTelemetrySnapshot, format_bytes

@dataclass(frozen=True, slots=True)
class HudCard:
    x: float; y: float; width: float; height: float
    mode: str; title: str; lines: tuple[str, ...]

def build_hud_card(bake: BakeSnapshot, telemetry: SystemTelemetrySnapshot,
                   *, mode="EXPANDED", anchor="BOTTOM_LEFT", scale=1.0,
                   viewport_width=800, viewport_height=600, hardware=True) -> HudCard:
    scale=max(.75,min(2.0,scale)); expanded=mode=="EXPANDED" and viewport_width >= 390*scale
    title={BakeJobKind.PREVIEW:"Cloth NeXt UI Preview",BakeJobKind.SOLVER_TEST:"Cloth NeXt Solver Test",BakeJobKind.BAKE:"Cloth NeXt Bake"}[bake.job_kind]
    frame=(f"Frame {bake.current_frame} / {bake.frame_end}" if bake.current_frame is not None and bake.frame_end else "Frame —")
    lines=[f"{frame}   {format_duration(bake.elapsed_seconds)}"]
    gpu=telemetry.primary_gpu
    if hardware:
        if gpu:
            util="Unavailable" if gpu.utilization_percent is None else f"{gpu.utilization_percent:.0f}%"
            lines.append(f"VRAM {format_bytes(gpu.vram_used_bytes)} / {format_bytes(gpu.vram_total_bytes)}   GPU {util}")
        else: lines.append("Telemetry unavailable")
    if expanded:
        lines.insert(0,bake.status_message or bake.status_title)
        if bake.estimated_remaining_seconds is not None:
            lines.append(f"Remaining  {format_duration(bake.estimated_remaining_seconds, approximate=True)}")
        if bake.active_object_name: lines.append(f"Object  {bake.active_object_name}")
        if bake.solver_mode: lines.append(f"Solver  {bake.solver_mode} {bake.solver_version}".rstrip())
        if bake.solver_process_id: lines.append(f"PID  {bake.solver_process_id}")
        if gpu:
            power="—" if gpu.power_watts is None else f"{gpu.power_watts:.0f} W"
            lines.append(f"GPU {gpu.index}  {gpu.name}   {gpu.temperature_c if gpu.temperature_c is not None else '—'} °C   {power}")
        for extra in telemetry.gpus:
            if extra is gpu: continue
            util="Unavailable" if extra.utilization_percent is None else f"{extra.utilization_percent:.0f}%"
            lines.append(f"GPU {extra.index}  {extra.name}   {format_bytes(extra.vram_used_bytes)}   {util}")
        cpu="Unavailable" if telemetry.cpu_utilization_percent is None else f"{telemetry.cpu_utilization_percent:.0f}%"
        lines.append(f"CPU {cpu}   RAM {format_bytes(telemetry.ram_used_bytes)} / {format_bytes(telemetry.ram_total_bytes)}")
        if telemetry.stale: lines.append("Telemetry stale")
    width=min(viewport_width-16*scale,(360 if expanded else 330)*scale)
    height=(42+len(lines)*19)*scale; margin=16*scale
    x=margin if anchor.endswith("LEFT") else viewport_width-width-margin
    y=viewport_height-height-margin if anchor.startswith("TOP") else margin
    return HudCard(max(0,x),max(0,y),max(1,width),max(1,min(height,viewport_height-2*margin)),"EXPANDED" if expanded else "COMPACT",title+" · "+bake.status_title,tuple(lines))
