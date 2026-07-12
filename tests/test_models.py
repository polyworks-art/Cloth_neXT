from dataclasses import FrozenInstanceError

import pytest

from cloth_next.ppf.models import (
    BackendCapabilities, BackendStatusSnapshot, ConnectionOwnership, ProjectId,
    ProtocolVersion, SchemaVersion, SolverConnection, UploadId,
)


@pytest.mark.parametrize("value_type", [ProjectId, UploadId, ProtocolVersion, SchemaVersion])
def test_value_objects_reject_empty_text(value_type):
    with pytest.raises(ValueError):
        value_type("   ")


def test_distinct_value_objects_do_not_compare_equal():
    assert ProjectId("same") != UploadId("same")
    assert ProtocolVersion("1") != SchemaVersion("1")


def test_backend_snapshot_is_immutable():
    snapshot = BackendStatusSnapshot(
        ProjectId("demo"), ProtocolVersion("0.11"), SchemaVersion("1"),
        BackendCapabilities(can_resume=True), "READY", (1, 2),
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.backend_status = "BUSY"


def test_only_owned_process_may_be_terminated():
    assert SolverConnection(ConnectionOwnership.OWNED_PROCESS).may_terminate_process
    assert not SolverConnection(ConnectionOwnership.EXTERNAL_SERVER).may_terminate_process

