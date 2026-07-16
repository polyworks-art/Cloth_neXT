# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Thread-safe state authority for panels, HUD, preview and companion IPC."""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock
import time
import uuid

from .status import BakeSnapshot, BakeState, normalized


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
    BakeState.PREPARING: ("scene validation", "Correct the highlighted Cloth NeXt scene setting, then retry."),
    BakeState.STARTING_COMPANION: ("Bake window startup", "Restart the Bake window or Blender, then retry."),
    BakeState.WAITING_FOR_COMPANION: ("Bake window handshake", "Close stale Bake windows and retry."),
    BakeState.COMPANION_READY: ("Bake workflow startup", "Retry the Bake; if it repeats, inspect the diagnostic log."),
    BakeState.STARTING_RUN: ("Bake worker startup", "Check cache-folder access and retry."),
    BakeState.EXPORTING: ("scene export", "Check evaluated Cloth and Collider geometry, then retry."),
    BakeState.STARTING_SOLVER: ("solver startup", "Check the installed solver and its diagnostic log, then retry."),
    BakeState.UPLOADING: ("scene upload", "Check the local solver connection and retry."),
    BakeState.BUILDING: ("solver project build", "Check scene geometry and the solver log, then retry."),
    BakeState.SIMULATING: ("simulation", "Inspect the reported frame and solver cause, adjust stability settings, then retry."),
    BakeState.FETCHING: ("result transfer", "Check the solver connection and available disk space, then retry."),
    BakeState.IMPORTING: ("playback cache import", "Check the cache path and Cloth object, then retry."),
    BakeState.CANCELLING: ("cancellation cleanup", "Wait for cleanup; restart Blender only if the process remains stuck."),
}

_ERROR_CODE = {
    BakeState.PREPARING: "CNX-E100",
    BakeState.STARTING_COMPANION: "CNX-E110",
    BakeState.WAITING_FOR_COMPANION: "CNX-E110",
    BakeState.COMPANION_READY: "CNX-E120",
    BakeState.STARTING_RUN: "CNX-E120",
    BakeState.EXPORTING: "CNX-E120",
    BakeState.STARTING_SOLVER: "CNX-E130",
    BakeState.UPLOADING: "CNX-E140",
    BakeState.BUILDING: "CNX-E150",
    BakeState.SIMULATING: "CNX-E160",
    BakeState.FETCHING: "CNX-E170",
    BakeState.IMPORTING: "CNX-E180",
    BakeState.CANCELLING: "CNX-E190",
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
                changes.setdefault("error_summary", "")
                changes.setdefault("error_details", "")
                changes.setdefault("error_code", "")
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

    def fail(self, summary: str, details: str = "") -> BakeSnapshot:
        current = self.snapshot()
        stage, action = _ERROR_STAGE.get(
            current.state, ("internal operation", "Open the diagnostic log and retry."))
        lines = [] if not details else [details]
        if "Stage:" not in details:
            lines.insert(0, f"Stage: {stage}")
        if "What to do:" not in details and "Recommended:" not in details:
            lines.append(f"What to do: {action}")
        return self.transition(
            BakeState.ERROR, error_summary=summary,
            error_details="\n".join(lines), status_message=summary,
            error_code=_ERROR_CODE.get(current.state,"CNX-E199"),
            activity_detail=stage)

    def reset(self) -> BakeSnapshot:
        state = self.snapshot().state
        if state is BakeState.IDLE:
            return self.snapshot()
        return self.transition(BakeState.IDLE, progress_current=0,
                               progress_total=None, preview=False,
                               status_message="Ready", job_id="",
                               error_code="")

    def subscribe(self, listener: Callable[[BakeSnapshot], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)


shared_controller = BakeController()
