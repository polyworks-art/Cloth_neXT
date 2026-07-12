# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit timer-driven UI demo; performs no solver or cache operations."""
from __future__ import annotations
import bpy
from ..bake.controller import InvalidTransition, shared_controller
from ..bake.status import BakeState

_running = False


def _tick():
    global _running
    if not _running:
        return None
    snap = shared_controller.snapshot()
    try:
        if snap.state is BakeState.PREPARING: shared_controller.transition(BakeState.EXPORTING, status_message="Previewing export UI")
        elif snap.state is BakeState.EXPORTING: shared_controller.transition(BakeState.STARTING_SOLVER, status_message="Previewing solver start UI")
        elif snap.state is BakeState.STARTING_SOLVER: shared_controller.transition(BakeState.SIMULATING, progress_current=0, progress_total=120, status_message="Previewing simulation UI")
        elif snap.state is BakeState.SIMULATING:
            if snap.progress_current < 120: shared_controller.update(progress_current=snap.progress_current + 4, current_frame=snap.progress_current + 4)
            else: shared_controller.transition(BakeState.IMPORTING, status_message="Previewing import UI")
        elif snap.state is BakeState.IMPORTING: shared_controller.transition(BakeState.FINISHED, status_message="UI preview finished — PPF was not run")
        elif snap.state is BakeState.CANCELLING: shared_controller.transition(BakeState.CANCELLED, status_message="UI preview cancelled")
        else: _running = False; return None
    except InvalidTransition:
        _running = False; return None
    return 0.15


def start(object_name=""):
    global _running
    if _running: return
    snap = shared_controller.snapshot()
    if snap.state is not BakeState.IDLE: shared_controller.reset()
    shared_controller.transition(BakeState.PREPARING, preview=True, active_object_name=object_name, status_message="UI PREVIEW — no PPF simulation")
    _running = True
    if not bpy.app.timers.is_registered(_tick): bpy.app.timers.register(_tick, first_interval=0.1)


def stop():
    global _running
    _running = False
    if bpy.app.timers.is_registered(_tick): bpy.app.timers.unregister(_tick)
    shared_controller.reset()
