# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake telemetry lifecycle used by memory safety without a viewport monitor."""
from __future__ import annotations

from ..bake.controller import shared_controller
from ..telemetry import shared_telemetry

_unsubscribe = None


def _on_snapshot(snapshot):
    shared_telemetry.set_enabled(bool(snapshot.active))


def register():
    global _unsubscribe
    if _unsubscribe is not None:
        return
    _on_snapshot(shared_controller.snapshot())
    shared_telemetry.start()
    _unsubscribe = shared_controller.subscribe(_on_snapshot)


def unregister():
    global _unsubscribe
    if _unsubscribe is not None:
        _unsubscribe()
        _unsubscribe = None
    shared_telemetry.set_enabled(False)
    shared_telemetry.stop()
