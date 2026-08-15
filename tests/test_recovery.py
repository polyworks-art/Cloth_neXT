from dataclasses import replace
import gzip
import json
import os
from pathlib import Path

import pytest

from cloth_next.recovery import (
    ProjectState, RecoveryIdentity, apply_retention, clear_checkpoints,
    compatibility, confirm_saved_states, create_project, evaluate_resumable,
    load_project, load_records, publish_checkpoint, reconcile_resumable,
    recovery_root, transition,
)


def identity(**changes):
    value = RecoveryIdentity(
        scene_key="scene", param_key="param", export_uuids=("a", "b"),
        geometry_fingerprint="geometry", topology_fingerprint="topology",
        frame_start=1, frame_end=180, fps=24.0,
        collider_sampling=(("c", 8),), solver_version="0.1.0",
        protocol_version="0.11", solver_schema_version="1")
    return replace(value, **changes)


def checkpoint(tmp_path: Path, name: str, payload=b"state") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_atomic_metadata_retries_transient_windows_replace_denial(
        tmp_path, monkeypatch):
    path = tmp_path / "metadata.json"
    path.write_text('{"old":true}', encoding="utf-8")
    real_replace = os.replace
    calls = []

    def flaky_replace(source, target):
        calls.append((source, target))
        if len(calls) < 3:
            raise PermissionError(13, "sharing violation")
        real_replace(source, target)

    monkeypatch.setattr("cloth_next.recovery.os.name", "nt")
    monkeypatch.setattr("cloth_next.recovery.os.replace", flaky_replace)
    monkeypatch.setattr("cloth_next.recovery.time.sleep", lambda _delay: None)

    from cloth_next.recovery import _atomic_json
    _atomic_json(path, {"new": True})

    assert len(calls) == 3
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}


def test_publish_reload_and_corruption_fail_closed(tmp_path):
    current = identity()
    path = checkpoint(tmp_path, "state.bin")
    publish_checkpoint(tmp_path, current, frame=20, project_id="p",
                       checkpoint_path=path)
    metadata = recovery_root(tmp_path, current.scene_key) / "metadata.json"
    assert load_records(metadata)[0].frame == 20
    path.write_bytes(b"corrupt")
    assert load_records(metadata) == ()


def test_schema_three_project_metadata_remains_resumable(tmp_path):
    project_root = tmp_path / "server" / "project"
    state = project_root / "session" / "output" / "state_20.bin.gz"
    state.parent.mkdir(parents=True)
    with gzip.open(state, "wb") as stream:
        stream.write(b"state")
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    record = confirm_saved_states(metadata, record, [20], keep=3)
    record = transition(metadata, record, ProjectState.RESUMABLE)
    raw = json.loads(metadata.read_text(encoding="utf-8"))
    raw["schema_version"] = 3
    raw["project"]["identity"]["recovery_schema_version"] = 3
    for checkpoint_record in raw["project"]["checkpoints"]:
        checkpoint_record["identity"]["recovery_schema_version"] = 3
    metadata.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(metadata)
    eligibility = evaluate_resumable(metadata, identity())

    assert loaded is not None
    assert loaded.identity.recovery_schema_version == 2
    assert [item.frame for item in loaded.checkpoints] == [20]
    assert eligibility.resumable


def test_geometry_and_params_compatibility():
    current = identity()
    assert compatibility(current, current).compatible
    assert not compatibility(
        current, replace(current, geometry_fingerprint="changed")).compatible
    changed = replace(current, param_key="changed")
    assert not compatibility(current, changed).compatible
    allowed = compatibility(current, changed, can_update_params=True)
    assert allowed.compatible and allowed.params_changed


def test_legacy_retention_drops_metadata_without_deleting_unowned_path(tmp_path):
    current = identity()
    paths = []
    for frame in (20, 40, 60):
        path = checkpoint(tmp_path, f"{frame}.bin", str(frame).encode())
        paths.append(path)
        publish_checkpoint(tmp_path, current, frame=frame, project_id="p",
                           checkpoint_path=path)
    metadata = recovery_root(tmp_path, current.scene_key) / "metadata.json"
    removed = apply_retention(metadata, 2)
    assert removed == ()
    assert paths[0].exists()
    assert [record.frame for record in load_records(metadata)] == [40, 60]


def test_legacy_clear_does_not_delete_unowned_checkpoint_or_result(tmp_path):
    current = identity()
    state = checkpoint(tmp_path, "state.bin")
    result = checkpoint(tmp_path, "result.pc2")
    publish_checkpoint(tmp_path, current, frame=20, project_id="p",
                       checkpoint_path=state)
    metadata = recovery_root(tmp_path, current.scene_key) / "metadata.json"
    clear_checkpoints(metadata)
    assert state.exists()
    assert result.exists()


def test_project_lifecycle_is_atomically_persisted(tmp_path):
    project_root = tmp_path / "server" / "project"
    project_root.mkdir(parents=True)
    metadata = tmp_path / "recovery" / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    reloaded = load_project(metadata)
    assert reloaded is not None
    assert reloaded.state is ProjectState.RUNNING
    assert reloaded.generation == 1
    assert not tuple(metadata.parent.glob(".*.tmp"))


def test_invalid_lifecycle_transition_is_rejected(tmp_path):
    project_root = tmp_path / "server" / "project"
    project_root.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    with pytest.raises(ValueError):
        transition(metadata, record, ProjectState.FINISHED)


def test_confirmed_state_is_published_before_retention(tmp_path):
    project_root = tmp_path / "server-data" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server-data", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    for frame in (20, 40, 60):
        (output / f"state_{frame}.bin.gz").write_bytes(
            gzip.compress(f"state-{frame}".encode()))
    record = confirm_saved_states(
        metadata, record, (20, 40, 60), keep=2)
    assert [item.frame for item in record.checkpoints] == [40, 60]
    assert not (output / "state_20.bin.gz").exists()
    assert (output / "state_40.bin.gz").exists()
    assert load_project(metadata).state is ProjectState.CHECKPOINT_CONFIRMED


def test_clear_checkpoints_refuses_metadata_path_outside_owned_project(tmp_path):
    metadata = tmp_path / "metadata.json"
    project_root = tmp_path / "server-data" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    legitimate = output / "state_20.bin.gz"
    legitimate.write_bytes(gzip.compress(b"state-20"))
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server-data", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    record = confirm_saved_states(metadata, record, (20,), keep=3)
    foreign = tmp_path / "artist-file.bin.gz"
    foreign.write_bytes(legitimate.read_bytes())
    forged = replace(record.checkpoints[0], checkpoint_path=str(foreign))
    transition(metadata, record, record.state, checkpoints=(forged,))

    clear_checkpoints(metadata)

    assert foreign.exists()


def test_missing_project_is_not_reported_resumable(tmp_path):
    project_root = tmp_path / "server" / "project"
    project_root.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    project_root.rmdir()
    record = load_project(metadata)
    assert record is not None
    assert record.state is ProjectState.ABANDONED
    assert record.error == "Recovery project missing"


def verified_project(tmp_path, *, state=ProjectState.CHECKPOINT_CONFIRMED,
                     frames=(20,), current=None):
    """Build a durable project whose checkpoints are authenticated on disk."""
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True, exist_ok=True)
    metadata = tmp_path / "recovery" / "metadata.json"
    current = current or identity()
    record = create_project(
        metadata, project_id="project", identity=current,
        server_data_root=tmp_path / "server", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    for frame in frames:
        (output / f"state_{frame}.bin.gz").write_bytes(
            gzip.compress(f"state-{frame}".encode()))
    record = confirm_saved_states(metadata, record, frames, keep=10)
    if state is not ProjectState.CHECKPOINT_CONFIRMED:
        if state is ProjectState.RESUMING:
            record = transition(metadata, record, ProjectState.RESUMABLE)
        record = transition(metadata, record, state)
    return metadata, current


def test_evaluate_resumable_reports_hard_aborted_running_project(tmp_path):
    # A host kill leaves the metadata mid-run; with a verified checkpoint the
    # evaluator must accept it even though no handler ever promoted the state.
    metadata, current = verified_project(
        tmp_path, state=ProjectState.RUNNING, frames=(40, 60))
    eligibility = evaluate_resumable(metadata, current)
    assert eligibility.available
    assert eligibility.resumable
    assert eligibility.compatible
    assert eligibility.state is ProjectState.RUNNING
    assert eligibility.latest_checkpoint_frame == 60
    assert eligibility.checkpoint_count == 2


def test_evaluate_resumable_accepts_cooperative_checkpoint_states(tmp_path):
    for state in (ProjectState.CHECKPOINT_REQUESTED,
                  ProjectState.CHECKPOINT_CONFIRMED,
                  ProjectState.SAVED, ProjectState.RESUMABLE,
                  ProjectState.RESUMING, ProjectState.FAILED):
        metadata, current = verified_project(tmp_path, state=state)
        eligibility = evaluate_resumable(metadata, current)
        assert eligibility.available, state
        assert eligibility.resumable, state


def test_reconcile_promotes_resuming_state(tmp_path):
    # A hard abort during a *resume* leaves the durable state RESUMING; with a
    # verified checkpoint it must still be recoverable afterwards.
    metadata, current = verified_project(
        tmp_path, state=ProjectState.RESUMING, frames=(40,))
    eligibility = evaluate_resumable(metadata, current)
    assert eligibility.available
    assert eligibility.resumable
    eligibility, promoted = reconcile_resumable(metadata, current)
    assert promoted
    assert eligibility.state is ProjectState.RESUMABLE
    record = load_project(metadata)
    assert record is not None
    assert record.state is ProjectState.RESUMABLE


def test_evaluate_resumable_without_identity_is_provisional(tmp_path):
    metadata, _current = verified_project(
        tmp_path, state=ProjectState.RUNNING)
    eligibility = evaluate_resumable(metadata)
    assert eligibility.available
    assert eligibility.compatible is None
    # Provisional: compatibility is unknown, so resumable falls back to the
    # durable on-disk truth. Callers must re-verify with an identity.
    assert eligibility.resumable


def test_evaluate_resumable_reports_incompatible_identity(tmp_path):
    metadata, _current = verified_project(
        tmp_path, state=ProjectState.RUNNING)
    changed = replace(identity(), geometry_fingerprint="changed")
    eligibility = evaluate_resumable(metadata, changed)
    assert eligibility.available
    assert not eligibility.compatible
    assert not eligibility.resumable
    assert "Geometry changed" in eligibility.reason


def test_evaluate_resumable_requires_verified_checkpoint(tmp_path):
    metadata, current = verified_project(
        tmp_path, state=ProjectState.RUNNING, frames=())
    eligibility = evaluate_resumable(metadata, current)
    assert not eligibility.available
    assert not eligibility.resumable
    assert eligibility.latest_checkpoint_frame == 0


def test_evaluate_resumable_rejects_new_and_finished(tmp_path):
    metadata, current = verified_project(
        tmp_path, state=ProjectState.FINISHED, frames=(20,))
    eligibility = evaluate_resumable(metadata, current)
    assert not eligibility.available
    assert not eligibility.resumable
    # A freshly created project (NEW) is never resumable.
    project_root = tmp_path / "fresh" / "project"
    project_root.mkdir(parents=True)
    fresh_metadata = tmp_path / "fresh" / "metadata.json"
    fresh = create_project(
        fresh_metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "fresh", project_root=project_root)
    assert not evaluate_resumable(fresh_metadata, fresh.identity).available


def test_evaluate_resumable_corrupt_checkpoint_fails_closed(tmp_path):
    metadata, current = verified_project(tmp_path, state=ProjectState.RUNNING)
    state_path = metadata.parent.parent / "server" / "project" \
        / "session" / "output" / "state_20.bin.gz"
    state_path.write_bytes(b"corrupt")
    eligibility = evaluate_resumable(metadata, current)
    assert not eligibility.available
    assert not eligibility.resumable


def test_evaluate_resumable_missing_metadata_fails_closed(tmp_path):
    eligibility = evaluate_resumable(tmp_path / "missing" / "metadata.json")
    assert not eligibility.available
    assert not eligibility.resumable
    assert eligibility.state is None
    assert "missing or invalid" in eligibility.reason


def test_reconcile_promotes_hard_aborted_state(tmp_path):
    metadata, current = verified_project(
        tmp_path, state=ProjectState.RUNNING, frames=(40,))
    eligibility, promoted = reconcile_resumable(metadata, current)
    assert promoted
    assert eligibility.resumable
    assert eligibility.state is ProjectState.RESUMABLE
    record = load_project(metadata)
    assert record is not None
    assert record.state is ProjectState.RESUMABLE


def test_reconcile_is_idempotent(tmp_path):
    metadata, current = verified_project(
        tmp_path, state=ProjectState.RUNNING, frames=(40,))
    reconcile_resumable(metadata, current)
    eligibility, promoted = reconcile_resumable(metadata, current)
    assert not promoted
    assert eligibility.state is ProjectState.RESUMABLE
    assert eligibility.resumable


def test_reconcile_refuses_corrupt_or_unavailable(tmp_path):
    metadata, current = verified_project(
        tmp_path, state=ProjectState.RUNNING, frames=(40,))
    state_path = metadata.parent.parent / "server" / "project" \
        / "session" / "output" / "state_40.bin.gz"
    state_path.write_bytes(b"corrupt")
    eligibility, promoted = reconcile_resumable(metadata, current)
    assert not promoted
    assert not eligibility.resumable
    assert load_project(metadata).state is ProjectState.RUNNING


def test_reconcile_refuses_finished_project(tmp_path):
    metadata, current = verified_project(
        tmp_path, state=ProjectState.FINISHED, frames=(40,))
    eligibility, promoted = reconcile_resumable(metadata, current)
    assert not promoted
    assert not eligibility.resumable
