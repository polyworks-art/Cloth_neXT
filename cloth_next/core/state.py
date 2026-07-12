"""Table-driven, side-effect-free application state machine."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from types import MappingProxyType
from typing import Mapping

from .errors import ErrorCategory, ErrorRecord
from .events import Event, SideEffectCommand


class ApplicationState(Enum):
    NOT_INSTALLED = auto()
    INSTALLING = auto()
    STOPPED = auto()
    STARTING = auto()
    READY = auto()
    TRANSFERRING = auto()
    SIMULATING = auto()
    PAUSED = auto()
    FETCHING_FRAMES = auto()
    CANCELLING = auto()
    UPDATING = auto()
    ERROR = auto()


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    current: ApplicationState
    previous: ApplicationState | None = None
    revision: int = 0
    error: ErrorRecord | None = None
    resumable: bool = False


@dataclass(frozen=True, slots=True)
class TransitionResult:
    snapshot: StateSnapshot
    commands: tuple[SideEffectCommand, ...] = ()
    accepted: bool = True


@dataclass(frozen=True, slots=True)
class TransitionRule:
    target: ApplicationState
    commands: tuple[SideEffectCommand, ...] = ()
    resumable: bool | None = None


_T = ApplicationState
_E = Event
_C = SideEffectCommand

_RULES: Mapping[tuple[ApplicationState, Event], TransitionRule] = MappingProxyType({
    (_T.NOT_INSTALLED, _E.INSTALL_REQUESTED): TransitionRule(_T.INSTALLING, (_C.INSTALL_SOLVER,)),
    (_T.INSTALLING, _E.INSTALL_SUCCEEDED): TransitionRule(_T.STOPPED),
    (_T.INSTALLING, _E.INSTALL_FAILED): TransitionRule(_T.ERROR),
    (_T.STOPPED, _E.START_REQUESTED): TransitionRule(_T.STARTING, (_C.START_BACKEND,)),
    (_T.STARTING, _E.START_SUCCEEDED): TransitionRule(_T.READY),
    (_T.STARTING, _E.START_FAILED): TransitionRule(_T.ERROR),
    (_T.READY, _E.TRANSFER_REQUESTED): TransitionRule(_T.TRANSFERRING, (_C.TRANSFER_SCENE,)),
    (_T.TRANSFERRING, _E.TRANSFER_SUCCEEDED): TransitionRule(_T.READY),
    (_T.TRANSFERRING, _E.TRANSFER_FAILED): TransitionRule(_T.ERROR),
    (_T.READY, _E.SIMULATION_REQUESTED): TransitionRule(_T.SIMULATING, (_C.START_SIMULATION,), False),
    (_T.PAUSED, _E.SIMULATION_REQUESTED): TransitionRule(_T.SIMULATING, (_C.START_SIMULATION,), False),
    (_T.SIMULATING, _E.RESUMABLE_STATE_SAVED): TransitionRule(_T.PAUSED, (), True),
    (_T.SIMULATING, _E.FETCH_REQUESTED): TransitionRule(_T.FETCHING_FRAMES, (_C.FETCH_FRAMES,)),
    (_T.PAUSED, _E.FETCH_REQUESTED): TransitionRule(_T.FETCHING_FRAMES, (_C.FETCH_FRAMES,)),
    (_T.READY, _E.FETCH_REQUESTED): TransitionRule(_T.FETCHING_FRAMES, (_C.FETCH_FRAMES,)),
    (_T.FETCHING_FRAMES, _E.FETCH_COMPLETED): TransitionRule(_T.READY),
    (_T.SIMULATING, _E.CANCEL_REQUESTED): TransitionRule(_T.CANCELLING, (_C.CANCEL_OPERATION,)),
    (_T.TRANSFERRING, _E.CANCEL_REQUESTED): TransitionRule(_T.CANCELLING, (_C.CANCEL_OPERATION,)),
    (_T.FETCHING_FRAMES, _E.CANCEL_REQUESTED): TransitionRule(_T.CANCELLING, (_C.CANCEL_OPERATION,)),
    (_T.CANCELLING, _E.CANCEL_COMPLETED): TransitionRule(_T.READY),
    (_T.READY, _E.STOPPED): TransitionRule(_T.STOPPED),
    (_T.PAUSED, _E.STOPPED): TransitionRule(_T.STOPPED, (), False),
    (_T.STOPPED, _E.UPDATE_REQUESTED): TransitionRule(_T.UPDATING, (_C.APPLY_UPDATE,)),
    (_T.UPDATING, _E.UPDATE_COMPLETED): TransitionRule(_T.STOPPED),
    (_T.ERROR, _E.RECOVER_TO_STOPPED): TransitionRule(_T.STOPPED),
    (_T.ERROR, _E.RECOVER_TO_READY): TransitionRule(_T.READY),
})


def transition(
    snapshot: StateSnapshot,
    event: Event,
    *,
    error: ErrorRecord | None = None,
) -> TransitionResult:
    if event is Event.OPERATION_FAILED and snapshot.current is not _T.ERROR:
        rule = TransitionRule(_T.ERROR)
    else:
        rule = _RULES.get((snapshot.current, event))
    if rule is None:
        invalid = ErrorRecord.create(
            category=ErrorCategory.INTERNAL,
            user_message="This action is not available in the current state.",
            technical_message=f"Invalid transition: {snapshot.current.name} + {event.name}",
            recommended_action="Wait for the current operation or use an available recovery action.",
            recoverable=True,
            context={"state": snapshot.current.name, "event": event.name},
        )
        return TransitionResult(snapshot=replace(snapshot, error=invalid), accepted=False)

    target_error = error if rule.target is _T.ERROR else None
    if rule.target is _T.ERROR and target_error is None:
        target_error = ErrorRecord.create(
            category=ErrorCategory.INTERNAL,
            user_message="The operation failed.",
            technical_message=f"{event.name} entered ERROR without a supplied error record",
            recommended_action="Review diagnostics and retry a recovery action.",
            recoverable=True,
        )
    resumable = snapshot.resumable if rule.resumable is None else rule.resumable
    next_snapshot = StateSnapshot(
        current=rule.target,
        previous=snapshot.current,
        revision=snapshot.revision + 1,
        error=target_error,
        resumable=resumable,
    )
    return TransitionResult(next_snapshot, rule.commands)


def transition_rules() -> Mapping[tuple[ApplicationState, Event], TransitionRule]:
    return _RULES

