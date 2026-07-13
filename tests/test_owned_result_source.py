from __future__ import annotations

import gc
import struct
from pathlib import Path

import numpy as np
import pytest

from cloth_next.ppf import results
from cloth_next.ppf_run.result_source import (
    OwnedLocalResultSource,
    UnsafeResultPath,
    contained_result_path,
    owned_project_root,
)


def _source(tmp_path: Path) -> OwnedLocalResultSource:
    root = tmp_path / "server-data" / "clothnext_abc123"
    (root / "session" / "output").mkdir(parents=True)
    return OwnedLocalResultSource(tmp_path, "clothnext_abc123",
                                  poll_interval=.001,
                                  readiness_timeout=.01)


def test_owned_paths_are_derived_and_contained(tmp_path):
    root = owned_project_root(tmp_path, "clothnext_abc123")
    assert root == (tmp_path / "server-data" / "clothnext_abc123").resolve()
    assert contained_result_path(root, results.MAP_PATH, "map.pickle") == (
        root / "session" / "map.pickle")
    assert contained_result_path(root, results.frame_file_path(7),
                                 "vert_7.bin") == (
        root / "session" / "output" / "vert_7.bin")
    for bad in ("../escape", "a/b", "C:\\escape"):
        with pytest.raises(UnsafeResultPath):
            owned_project_root(tmp_path, bad)
    with pytest.raises(UnsafeResultPath):
        contained_result_path(root, "session/output/../secret.bin", "secret.bin")


def test_symlink_escape_is_rejected(tmp_path):
    root = owned_project_root(tmp_path, "clothnext_abc123")
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "session"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks is not permitted on this Windows host")
    with pytest.raises(UnsafeResultPath):
        contained_result_path(root, results.MAP_PATH, "map.pickle")


def test_final_frame_mmap_validates_and_closes_handle(tmp_path):
    source = _source(tmp_path)
    path = source.root / results.frame_file_path(1)
    expected = np.arange(18, dtype="<f4").reshape(6, 3)
    path.write_bytes(memoryview(expected).cast("B"))
    with source.frame_positions(1, 6, lambda: None) as frame_data:
        assert np.array_equal(frame_data.positions, expected)
    replacement = path.with_suffix(".moved")
    path.rename(replacement)
    replacement.unlink()
    assert source.bytes_read == expected.nbytes


def test_tmp_frame_is_ignored_and_missing_final_times_out(tmp_path):
    source = _source(tmp_path)
    temporary = source.root / "session" / "output" / "vert_1.bin.tmp"
    temporary.write_bytes(bytes(24))
    with pytest.raises(results.ResultValidationError, match="not ready"):
        with source.frame_positions(1, 2, lambda: None):
            pass


@pytest.mark.parametrize("payload, vertices, message", [
    (b"bad", 1, "multiple of 12"),
    (bytes(12), 2, "not ready"),
    (struct.pack("<3f", float("nan"), 0, 0), 1, "non-finite"),
])
def test_frame_validation_and_exception_cleanup(tmp_path, payload, vertices,
                                                message):
    source = _source(tmp_path)
    path = source.root / results.frame_file_path(1)
    path.write_bytes(payload)
    with pytest.raises(results.ResultValidationError, match=message):
        with source.frame_positions(1, vertices, lambda: None):
            raise AssertionError("unreachable")
    gc.collect()
    path.unlink()


def test_contiguous_and_gather_extractors_match_and_reuse_buffer():
    frame = np.arange(30, dtype="<f4").reshape(10, 3)
    contiguous = results.ObjectFrameExtractor.create(
        np.array([3, 4, 5], dtype=np.intp), expected_count=3, uuid="cloth")
    sliced = contiguous.extract(frame, frame=1)
    assert np.shares_memory(sliced, frame)
    assert np.array_equal(sliced, frame[[3, 4, 5]])

    gather = results.ObjectFrameExtractor.create(
        np.array([5, 1, 8], dtype=np.intp), expected_count=3, uuid="cloth")
    first = gather.extract(frame, frame=1)
    second = gather.extract(frame + 1, frame=2)
    assert first is second
    assert np.array_equal(second, (frame + 1)[[5, 1, 8]])
