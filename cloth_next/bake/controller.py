# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Thread-safe state authority for panels, HUD, preview and companion IPC."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
import time
import uuid

from .error_presentation import present_error
from .status import BakeSnapshot, BakeState, normalized
from ..core.error_codes import classify_error, valid_error_code


class InvalidTransition(ValueError):
    pass


_NEXT = {
    BakeState.IDLE: {BakeState.PREPARING},
    BakeState.PREPARING: {BakeState.STARTING_COMPANION, BakeState.STARTING_RUN,
                          BakeState.EXPORTING, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.STARTING_COMPANION: {BakeState.WAITING_FOR_COMPANION,
                                   BakeState.CANCELLING, BakeState.ERROR},
    BakeState.WAITING_FOR_COMPANION: {BakeState.COMPANION_READY,
                                      BakeState.CANCELLING, BakeState.ERROR},
    BakeState.COMPANION_READY: {BakeState.STARTING_RUN, BakeState.CANCELLING,
                                BakeState.ERROR},
    BakeState.STARTING_RUN: {BakeState.EXPORTING, BakeState.CANCELLING,
                             BakeState.ERROR},
    BakeState.EXPORTING: {BakeState.STARTING_SOLVER, BakeState.CANCELLING, BakeState.ERROR},
    # STARTING_SOLVER -> SIMULATING stays for the display-only UI preview;
    # the real run goes through UPLOADING and BUILDING.
    BakeState.STARTING_SOLVER: {BakeState.UPLOADING, BakeState.SIMULATING,
                                BakeState.CANCELLING, BakeState.ERROR},
    BakeState.UPLOADING: {BakeState.BUILDING, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.BUILDING: {BakeState.SIMULATING, BakeState.FETCHING,
                         BakeState.CANCELLING, BakeState.ERROR},
    # Simulation and incremental frame download interleave.
    BakeState.SIMULATING: {BakeState.FETCHING, BakeState.IMPORTING,
                           BakeState.CANCELLING, BakeState.ERROR},
    BakeState.FETCHING: {BakeState.SIMULATING, BakeState.IMPORTING,
                         BakeState.CANCELLING, BakeState.ERROR},
    BakeState.IMPORTING: {BakeState.FINISHED, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.CANCELLING: {BakeState.CANCELLED, BakeState.ERROR},
    BakeState.FINISHED: {BakeState.IDLE, BakeState.PREPARING},
    BakeState.CANCELLED: {BakeState.IDLE, BakeState.PREPARING},
    BakeState.ERROR: {BakeState.IDLE, BakeState.PREPARING},
}

_ERROR_STAGE = {
    BakeState.PREPARING: ("Scene validation", "Correct the highlighted Cloth NeXt scene setting, then retry."),
    BakeState.STARTING_COMPANION: ("Bake window startup", "Restart the Bake window or Blender, then retry."),
    BakeState.WAITING_FOR_COMPANION: ("Bake window connection", "Close stale Bake windows and retry."),
    BakeState.COMPANION_READY: ("Bake startup", "Retry the Bake. If it repeats, report the error code."),
    BakeState.STARTING_RUN: ("Bake worker startup", "Check the cache folder and retry."),
    BakeState.EXPORTING: ("Scene export", "Check evaluated Cloth and Collider geometry, then retry."),
    BakeState.STARTING_SOLVER: ("Solver startup", "Run the solver health check, then retry."),
    BakeState.UPLOADING: ("Scene transfer", "Check the local solver connection and retry."),
    BakeState.BUILDING: ("Scene preparation", "Check geometry, materials, and Pins, then retry."),
    BakeState.SIMULATING: ("Simulation", "Check the reported frame and apply the suggested stability change."),
    BakeState.FETCHING: ("Result transfer", "Check the solver connection and available disk space, then retry."),
    BakeState.IMPORTING: ("Playback cache", "Check the cache path and Cloth object, then retry."),
    BakeState.CANCELLING: ("Cancellation cleanup", "Wait for cleanup. Restart Blender only if it remains stuck."),
}


class BakeController:
    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshot = BakeSnapshot(updated_at=time.time())
        self._listeners: set[Callable[[BakeSnapshot], None]] = set()

    def snapshot(self) -> BakeSnapshot:
        with self._lock:
            return self._snapshot

    def transition(self, state: BakeState, **changes) -> BakeSnapshot:
        with self._lock:
            old = self._snapshot
            if state != old.state and state not in _NEXT[old.state]:
                raise InvalidTransition(f"{old.state.value} -> {state.value}")
            if state is BakeState.PREPARING:
                changes.setdefault("job_id", uuid.uuid4().hex)
                changes.setdefault("elapsed_seconds", 0.0)
                changes.setdefault("estimated_remaining_seconds", None)
                changes.setdefault("progress_current", 0)
                changes.setdefault("progress_total", None)
                changes.setdefault("current_frame", None)
                changes.setdefault("frame_start", None)
                changes.setdefault("frame_end", None)
                changes.setdefault("active_object_name", "")
                changes.setdefault("error_summary", "")
                changes.setdefault("error_details", "")
                changes.setdefault("error_code", "")
                changes.setdefault("solver_mode", "")
                changes.setdefault("solver_version", "")
                changes.setdefault("solver_process_id", None)
                changes.setdefault("activity_label", "")
                changes.setdefault("activity_detail", "")
                changes.setdefault("can_pause", False)
                changes.setdefault("is_paused", False)
            self._snapshot = normalized(old, state=state, **changes)
            listeners = tuple(self._listeners)
            result = self._snapshot
        for listener in listeners:
            listener(result)
        return result

    def update(self, **changes) -> BakeSnapshot:
        return self.transition(self.snapshot().state, **changes)

    def request_cancel(self) -> BakeSnapshot:
        return self.transition(BakeState.CANCELLING,
                               status_message="Cancellation requested")

    def fail(self, summary: str, details: str = "", *,
             error_code: str = "") -> BakeSnapshot:
        """Publish only bounded artist text; callers keep diagnostics in logs."""
        current = self.snapshot()
        stage, action = _ERROR_STAGE.get(
            current.state,
            ("Cloth NeXt", "Retry once. If it repeats, report the error code."))
        code = (error_code if valid_error_code(error_code)
                else classify_error(current.state, summary, details))
        presentation = present_error(
            summary, details, error_code=code, stage=stage, action=action)
        return self.transition(
            BakeState.ERROR,
            error_summary=presentation.summary,
            error_details=presentation.details,
            status_message=presentation.summary,
            error_code=code,
            estimated_remaining_seconds=None,
            activity_detail=stage)

    def reset(self) -> BakeSnapshot:
        state = self.snapshot().state
        if state is BakeState.IDLE:
            return self.snapshot()
        return self.transition(BakeState.IDLE, progress_current=0,
                               progress_total=None, preview=False,
                               status_message="Ready", job_id="",
                               error_code="",
                               estimated_remaining_seconds=None)

    def subscribe(self, listener: Callable[[BakeSnapshot], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)


shared_controller = BakeController()
