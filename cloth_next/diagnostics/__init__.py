# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in diagnostics upload (performance, Bake results, errors, crashes). No ``bpy``."""
from .service import DiagnosticsLogHandler, DiagnosticsService

__all__ = ("DiagnosticsLogHandler", "DiagnosticsService")
