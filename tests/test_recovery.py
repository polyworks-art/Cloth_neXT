from dataclasses import replace
import gzip
from pathlib import Path

import pytest

from cloth_next.recovery import (
    ProjectState, RecoveryIdentity, apply_retention, clear_checkpoints,
    compatibility, confirm_saved_states, create_project,
    discover_checkpoint_frames, load_project, load_records,
    publish_checkpoint, publish_partial_caches, recovery_root, transition,
    verified_partial_cache,
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
    rejected = compatibility(current, changed)
    assert not rejected.compatible
    assert "Pin" in rejected.reason
    assert "Time Scale" in rejected.reason
    allowed = compatibility(current, changed, can_update_params=True)
    assert allowed.compatible and allowed.params_changed


def test_compatibility_reports_specific_scene_boundary():
    current = identity()
    assert compatibility(
        current, replace(
            current, topology_fingerprint="changed")).reason == (
                "Topology changed")
    assert "Animated Collider" in compatibility(
        current, replace(current, scene_key="changed")).reason


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


def test_newest_complete_checkpoint_wins_over_newer_truncated_file(tmp_path):
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    (output / "state_11.bin.gz").write_bytes(gzip.compress(b"valid-11"))
    damaged = gzip.compress(b"damaged-12" * 128)
    (output / "state_12.bin.gz").write_bytes(damaged[:-4])

    record = confirm_saved_states(metadata, record, (11, 12), keep=1)

    assert [item.frame for item in record.checkpoints] == [11]
    assert record.last_frame == 11


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


def test_restart_discovers_newer_checkpoint_without_losing_resumable_state(
        tmp_path):
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    metadata = tmp_path / "metadata.json"
    record = create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root)
    record = transition(metadata, record, ProjectState.RUNNING)
    (output / "state_4.bin.gz").write_bytes(gzip.compress(b"state-4"))
    record = confirm_saved_states(metadata, record, (4,), keep=3)
    record = transition(metadata, record, ProjectState.SAVED)
    record = transition(metadata, record, ProjectState.RESUMABLE)
    (output / "state_7.bin.gz").write_bytes(gzip.compress(b"state-7"))

    reloaded = load_project(metadata)
    discovered = confirm_saved_states(metadata, reloaded, (), keep=3)

    assert discovered.state is ProjectState.RESUMABLE
    assert [item.frame for item in discovered.checkpoints] == [4, 7]
    assert discovered.last_frame == 7


def test_partial_cache_integrity_survives_restart_and_rejects_tampering(
        tmp_path):
    project_root = tmp_path / "server" / "project"
    project_root.mkdir(parents=True)
    partial = tmp_path / "partials" / "cloth.pc2.partial"
    partial.parent.mkdir()
    partial.write_bytes(b"authenticated partial bytes")
    metadata = tmp_path / "metadata.json"
    create_project(
        metadata, project_id="project", identity=identity(),
        server_data_root=tmp_path / "server", project_root=project_root,
        partial_pc2=(("cloth", str(partial)),))

    published = publish_partial_caches(
        metadata, (("cloth", partial, 4),))
    reloaded = load_project(metadata)

    assert reloaded.partial_caches == published.partial_caches
    assert verified_partial_cache(
        reloaded, "cloth", partial).frame_count == 4
    partial.write_bytes(b"x" * len(partial.read_bytes()))
    assert verified_partial_cache(reloaded, "cloth", partial) is None


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
