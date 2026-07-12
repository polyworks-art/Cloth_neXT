# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure, cached hardware telemetry; never imports Blender."""
from .service import TelemetryService, shared_telemetry
from .snapshot import GpuTelemetry, SystemTelemetrySnapshot

__all__ = ("GpuTelemetry", "SystemTelemetrySnapshot", "TelemetryService",
           "shared_telemetry")
