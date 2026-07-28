from dataclasses import replace
import gzip
from pathlib import Path

import pytest

from cloth_next.recovery import (
    ProjectState, RecoveryIdentity, apply_retention, clear_checkpoints,
    compatibility, confirm_saved_states, create_project,
    discover_checkpoint_frames, load_project, load_records,
    publish_checkpoint, recovery_root, transition,
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


def test_publish_reload_and_corruption_fail_closed(tmp_path):
    current = identity()
    path = checkpoint(tmp_path, "state.bin")
    publish_checkpoint(tmp_path, current, frame=20, project_id="p",
                       checkpoint_path=path)
    metadata = recovery_root(tmp_path, current.scene_key) / "metadata.json"
    assert load_records(metadata)[0].frame == 20
    path.write_bytes(b"corrupt")
    assert load_records(metadata) == ()


def test_geometry_and_params_compatibility():
    current = identity()
    assert compatibility(current, current).compatible
    assert not compatibility(
        current, replace(current, geometry_fingerprint="changed")).compatible
    changed = replace(current, param_key="changed")
    assert not compatibility(current, changed).compatible
    allowed = compatibility(current, changed, can_update_params=True)
    assert allowed.compatible and allowed.params_changed


def test_retention_publishes_metadata_before_deleting_old(tmp_path):
    current = identity()
    paths = []
    for frame in (20, 40, 60):
        path = checkpoint(tmp_path, f"{frame}.bin", str(frame).encode())
        paths.append(path)
        publish_checkpoint(tmp_path, current, frame=frame, project_id="p",
                           checkpoint_path=path)
    metadata = recovery_root(tmp_path, current.scene_key) / "metadata.json"
    removed = apply_retention(metadata, 2)
    assert removed == (paths[0],)
    assert [record.frame for record in load_records(metadata)] == [40, 60]


def test_clear_checkpoints_does_not_touch_result(tmp_path):
    current = identity()
    state = checkpoint(tmp_path, "state.bin")
    result = checkpoint(tmp_path, "result.pc2")
    publish_checkpoint(tmp_path, current, frame=20, project_id="p",
                       checkpoint_path=state)
    metadata = recovery_root(tmp_path, current.scene_key) / "metadata.json"
    clear_checkpoints(metadata)
    assert not state.exists()
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
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
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


def test_checkpoint_is_discovered_when_status_response_lags(tmp_path):
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    (output / "state_17.bin.gz").write_bytes(gzip.compress(b"state-17"))

    assert discover_checkpoint_frames(project_root) == (17,)
    record = confirm_saved_states(metadata, record, (), keep=3)

    assert [item.frame for item in record.checkpoints] == [17]
    assert record.last_frame == 17


def test_truncated_gzip_checkpoint_is_never_published(tmp_path):
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    payload = gzip.compress(b"large-enough-state" * 256)
    (output / "state_9.bin.gz").write_bytes(payload[:-8])

    record = confirm_saved_states(metadata, record, (9,), keep=3)

    assert record.checkpoints == ()
    assert record.last_frame == 0


def test_stale_transition_cannot_erase_new_checkpoint(tmp_path):
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    stale = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    stale = transition(metadata, stale, ProjectState.RUNNING)
    (output / "state_23.bin.gz").write_bytes(gzip.compress(b"state-23"))
    confirmed = confirm_saved_states(metadata, stale, (23,), keep=3)
    assert confirmed.checkpoints

    requested = transition(
        metadata, stale, ProjectState.CHECKPOINT_REQUESTED)

    assert [item.frame for item in requested.checkpoints] == [23]
    assert load_project(metadata).checkpoints[0].frame == 23


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
