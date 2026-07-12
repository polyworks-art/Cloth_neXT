# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class GpuTelemetry:
    index: int
    name: str
    utilization_percent: float | None
    vram_used_bytes: int | None
    vram_total_bytes: int | None
    temperature_c: float | None
    power_watts: float | None

@dataclass(frozen=True, slots=True)
class SystemTelemetrySnapshot:
    gpus: tuple[GpuTelemetry, ...] = ()
    cpu_utilization_percent: float | None = None
    ram_used_bytes: int | None = None
    ram_total_bytes: int | None = None
    solver_process_id: int | None = None
    solver_process_memory_bytes: int | None = None
    updated_at: float = 0.0
    stale: bool = False
    error_summary: str = "Telemetry unavailable"

    @property
    def primary_gpu(self) -> GpuTelemetry | None:
        return max(self.gpus, key=lambda g: g.vram_used_bytes or -1,
                   default=None)

def format_bytes(value: int | None) -> str:
    if value is None: return "Unavailable"
    for suffix, divisor in (("TB", 1 << 40), ("GB", 1 << 30),
                            ("MB", 1 << 20), ("KB", 1 << 10)):
        if value >= divisor: return f"{value / divisor:.1f} {suffix}"
    return f"{value} B"
