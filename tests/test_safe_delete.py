from pathlib import Path

import pytest

from cloth_next.core import safe_delete


def test_immediate_successful_deletion(tmp_path):
    path = tmp_path / "owned.tmp"
    path.write_bytes(b"temporary")

    result = safe_delete.delete_owned(
        path, root=tmp_path, ownership_authenticated=True,
        lifecycle_stage="TEST", artifact_type="temporary")

    assert result.success and result.removed
    assert result.attempts == 1 and result.retries == 0
    assert not path.exists()


def test_already_missing_is_success_and_repeated_calls_are_idempotent(tmp_path):
    path = tmp_path / "missing.partial"

    first = safe_delete.delete_owned(
        path, root=tmp_path, ownership_authenticated=True,
        lifecycle_stage="TEST", artifact_type="partial")
    second = safe_delete.delete_owned(
        path, root=tmp_path, ownership_authenticated=True,
        lifecycle_stage="TEST", artifact_type="partial")

    assert first.success and first.missing
    assert second.success and second.missing


def test_transient_windows_failure_retries_then_succeeds(tmp_path, monkeypatch):
    path = tmp_path / "locked.tmp"
    path.write_bytes(b"temporary")
    real_delete = safe_delete._delete_once
    calls = []

    def transient(target, *, recursive):
        calls.append(target)
        if len(calls) < 3:
            raise PermissionError("simulated Windows sharing violation")
        return real_delete(target, recursive=recursive)

    monkeypatch.setattr(safe_delete, "_delete_once", transient)
    result = safe_delete.delete_owned(
        path, root=tmp_path, ownership_authenticated=True,
        lifecycle_stage="TEST", artifact_type="temporary",
        backoff_seconds=(0, 0, 0), windows=True)

    assert result.success and result.removed
    assert result.attempts == 3 and result.retries == 2
    assert not path.exists()


def test_retry_exhaustion_reports_lock_without_unbounded_retry(
        tmp_path, monkeypatch):
    path = tmp_path / "locked.partial"
    path.write_bytes(b"partial")
    calls = []

    def locked(target, *, recursive):
        calls.append((target, recursive))
        raise PermissionError("simulated Windows sharing violation")

    monkeypatch.setattr(safe_delete, "_delete_once", locked)
    result = safe_delete.delete_owned(
        path, root=tmp_path, ownership_authenticated=True,
        lifecycle_stage="CANCEL", artifact_type="partial",
        tombstone=False, backoff_seconds=(0, 0), windows=True)

    assert not result.success
    assert result.failure_kind is safe_delete.DeleteFailureKind.SHARING_VIOLATION
    assert result.attempts == 3 and result.retries == 2
    assert len(calls) == 3
    assert "stage=CANCEL" in result.technical_diagnostic()


def test_tombstone_rename_then_later_cleanup(tmp_path, monkeypatch):
    path = tmp_path / "cache.pc2.partial"
    path.write_bytes(b"partial")
    real_delete = safe_delete._delete_once

    def locked(target, *, recursive):
        del recursive
        raise PermissionError("simulated Windows sharing violation")

    monkeypatch.setattr(safe_delete, "_delete_once", locked)
    result = safe_delete.delete_owned(
        path, root=tmp_path, ownership_authenticated=True,
        lifecycle_stage="BAKE_START", artifact_type="partial",
        backoff_seconds=(), windows=True)

    assert result.success and result.tombstoned
    assert result.tombstone_path is not None
    assert not path.exists() and result.tombstone_path.exists()

    monkeypatch.setattr(safe_delete, "_delete_once", real_delete)
    cleanup = safe_delete.cleanup_tombstones(
        tmp_path, ownership_authenticated=True,
        lifecycle_stage="NEXT_BAKE", recursive=False)

    assert len(cleanup) == 1 and cleanup[0].removed
    assert not result.tombstone_path.exists()


def test_unsafe_or_unowned_paths_are_rejected(tmp_path):
    outside = tmp_path.parent / "outside.tmp"
    outside.write_bytes(b"artist")
    with pytest.raises(safe_delete.UnsafeDeleteError):
        safe_delete.delete_owned(
            outside, root=tmp_path, ownership_authenticated=True,
            lifecycle_stage="TEST", artifact_type="temporary")
    with pytest.raises(safe_delete.UnsafeDeleteError):
        safe_delete.delete_owned(
            tmp_path / "legacy.pc2", root=tmp_path,
            ownership_authenticated=False, lifecycle_stage="TEST",
            artifact_type="playback_cache")
    assert outside.read_bytes() == b"artist"
