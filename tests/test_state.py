from dataclasses import FrozenInstanceError

import pytest

from cloth_next.core.events import Event
from cloth_next.core.state import ApplicationState, StateSnapshot, transition, transition_rules


@pytest.mark.parametrize("source_event, rule", list(transition_rules().items()))
def test_every_declared_transition(source_event, rule):
    source, event = source_event
    result = transition(StateSnapshot(source), event)
    assert result.accepted
    assert result.snapshot.current is rule.target
    assert result.snapshot.previous is source
    assert result.snapshot.revision == 1
    assert result.commands == rule.commands


@pytest.mark.parametrize("state,event", [
    (ApplicationState.NOT_INSTALLED, Event.SIMULATION_REQUESTED),
    (ApplicationState.READY, Event.INSTALL_REQUESTED),
    (ApplicationState.SIMULATING, Event.UPDATE_REQUESTED),
    (ApplicationState.STOPPED, Event.FETCH_REQUESTED),
])
def test_invalid_transitions_are_rejected_without_changing_state(state, event):
    original = StateSnapshot(state, revision=7)
    result = transition(original, event)
    assert not result.accepted
    assert result.snapshot.current is state
    assert result.snapshot.revision == 7
    assert result.snapshot.error is not None


def test_paused_requires_explicit_resumable_state_event():
    result = transition(StateSnapshot(ApplicationState.SIMULATING), Event.RESUMABLE_STATE_SAVED)
    assert result.snapshot.current is ApplicationState.PAUSED
    assert result.snapshot.resumable
    assert not any(
        rule.target is ApplicationState.PAUSED and event is not Event.RESUMABLE_STATE_SAVED
        for (_state, event), rule in transition_rules().items()
    )


def test_snapshot_is_immutable():
    snapshot = StateSnapshot(ApplicationState.READY)
    with pytest.raises(FrozenInstanceError):
        snapshot.current = ApplicationState.STOPPED

