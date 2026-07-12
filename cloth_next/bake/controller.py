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
    BakeState.PREPARING: {BakeState.EXPORTING, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.EXPORTING: {BakeState.STARTING_SOLVER, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.STARTING_SOLVER: {BakeState.SIMULATING, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.SIMULATING: {BakeState.IMPORTING, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.IMPORTING: {BakeState.FINISHED, BakeState.CANCELLING, BakeState.ERROR},
    BakeState.CANCELLING: {BakeState.CANCELLED, BakeState.ERROR},
    BakeState.FINISHED: {BakeState.IDLE, BakeState.PREPARING},
    BakeState.CANCELLED: {BakeState.IDLE, BakeState.PREPARING},
    BakeState.ERROR: {BakeState.IDLE, BakeState.PREPARING},
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
        return self.transition(BakeState.ERROR, error_summary=summary,
                               error_details=details, status_message=summary)

    def reset(self) -> BakeSnapshot:
        state = self.snapshot().state
        if state is BakeState.IDLE:
            return self.snapshot()
        return self.transition(BakeState.IDLE, progress_current=0,
                               progress_total=None, preview=False,
                               status_message="Ready", job_id="")

    def subscribe(self, listener: Callable[[BakeSnapshot], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)


shared_controller = BakeController()
