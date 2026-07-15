# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure layout and history model for the viewport resource graphs."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .snapshot import SystemTelemetrySnapshot, format_bytes


def _fraction(used, total):
    if used is None or total is None or total <= 0:
        return None
    return max(0.0, min(1.0, float(used) / float(total)))


@dataclass(frozen=True, slots=True)
class ResourceMetric:
    key: str
    label: str
    value: str
    fraction: float | None


@dataclass(frozen=True, slots=True)
class ResourceCard:
    x: float
    y: float
    width: float
    height: float
    scale: float
    metrics: tuple[ResourceMetric, ...]


class ResourceHistory:
    """One sample per telemetry refresh, never one duplicate per UI redraw."""

    def __init__(self, length: int = 60):
        self.series = {key: deque(maxlen=length)
                       for key in ("cpu", "ram", "vram")}
        self.updated_at = None

    def sample(self, telemetry: SystemTelemetrySnapshot):
        if telemetry.updated_at == self.updated_at:
            return False
        self.updated_at = telemetry.updated_at
        for metric in resource_metrics(telemetry):
            self.series[metric.key].append(metric.fraction)
        return True

    def clear(self):
        for values in self.series.values():
            values.clear()
        self.updated_at = None


def resource_metrics(telemetry: SystemTelemetrySnapshot):
    gpu = telemetry.primary_gpu
    cpu_fraction = (None if telemetry.cpu_utilization_percent is None else
                    max(0.0, min(1.0,
                                     telemetry.cpu_utilization_percent / 100.0)))
    cpu_value = ("Unavailable" if telemetry.cpu_utilization_percent is None
                 else f"{telemetry.cpu_utilization_percent:.0f}%")
    ram_fraction = _fraction(telemetry.ram_used_bytes,
                             telemetry.ram_total_bytes)
    ram_value = ("Unavailable" if ram_fraction is None else
                 f"{format_bytes(telemetry.ram_used_bytes)} / "
                 f"{format_bytes(telemetry.ram_total_bytes)}")
    vram_fraction = _fraction(getattr(gpu, "vram_used_bytes", None),
                              getattr(gpu, "vram_total_bytes", None))
    vram_value = ("Unavailable" if vram_fraction is None else
                  f"{format_bytes(gpu.vram_used_bytes)} / "
                  f"{format_bytes(gpu.vram_total_bytes)}")
    return (ResourceMetric("cpu", "CPU", cpu_value, cpu_fraction),
            ResourceMetric("ram", "RAM", ram_value, ram_fraction),
            ResourceMetric("vram", "VRAM", vram_value, vram_fraction))


def build_resource_card(telemetry: SystemTelemetrySnapshot, *,
                        anchor="BOTTOM_LEFT", scale=1.0,
                        viewport_width=800, viewport_height=600):
    scale = max(0.75, min(2.0, scale))
    margin = 16 * scale
    width = min(360 * scale, max(1, viewport_width - 2 * margin))
    height = min(184 * scale, max(1, viewport_height - 2 * margin))
    x = margin if anchor.endswith("LEFT") else viewport_width - width - margin
    y = (viewport_height - height - margin
         if anchor.startswith("TOP") else margin)
    return ResourceCard(max(0, x), max(0, y), width, height, scale,
                        resource_metrics(telemetry))
