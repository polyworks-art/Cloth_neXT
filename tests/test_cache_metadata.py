# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json

import pytest

from cloth_next.bake import cache_metadata, pc2


def _partial(path, *, settings="settings-a", geometry="geometry-a"):
    return cache_metadata.partial_metadata(
        cache_path=path,
        fingerprints={
            "settings": settings,
            "geometry": geometry,
            "combined": "combined-a",
            "topology": "topology-a",
            "object": "object-a",
            "scene": "scene-a",
        },
        identities={
            "cloth_next_version": "1.2.3",
            "blender_version": "5.2.0",
            "object": {"object_key": "object-1"},
            "solver": {"package_version": "0.1.0",
                       "protocol_version": "0.11", "schema_version": "1"},
        },
        expected={"vertex_count": 2, "frame_count": 2,
                  "start_frame": 0.0, "sample_rate": 1.0},
        details={"frame_start": 1, "frame_end": 2})


def _publish(tmp_path):
    path = tmp_path / "cn_test_cloth_cache.pc2"
    pc2.write_pc2(path, [((0, 0, 0), (1, 0, 0)),
                         ((0, 0, -1), (1, 0, -1))])
    partial = _partial(path)
    complete = cache_metadata.completed_metadata(
        partial, cache_path=path, timings={"total": 1.25})
    cache_metadata.write_atomic(cache_metadata.sidecar_path(path), complete)
    return path, complete


def test_deterministic_hash_ignores_mapping_insertion_order():
    first = {"b": 2, "a": {"y": 2, "x": 1}}
    second = {"a": {"x": 1, "y": 2}, "b": 2}
    assert cache_metadata.canonical_json(first) == \
        cache_metadata.canonical_json(second)
    assert cache_metadata.deterministic_hash(first) == \
        cache_metadata.deterministic_hash(second)


def test_partial_sidecar_is_never_usable(tmp_path):
    path = tmp_path / "cn_test_cloth_partial.pc2"
    cache_metadata.write_atomic(cache_metadata.sidecar_path(path),
                                _partial(path))
    result = cache_metadata.inspect_cache(path)
    assert result.condition is cache_metadata.CacheCondition.PARTIAL
    assert not result.usable


def test_complete_pair_authenticates_all_bytes_and_layout(tmp_path):
    path, metadata = _publish(tmp_path)
    result = cache_metadata.inspect_cache(
        path, settings_fingerprint="settings-a",
        geometry_fingerprint="geometry-a")
    assert result.condition is cache_metadata.CacheCondition.READY
    assert result.usable
    assert result.metadata["metadata_digest"] == metadata["metadata_digest"]
    assert result.metadata["cache_sha256"] == cache_metadata.file_sha256(path)


@pytest.mark.parametrize("fingerprint,condition", [
    (("settings-b", "geometry-a"),
     cache_metadata.CacheCondition.STALE_SETTINGS),
    (("settings-a", "geometry-b"),
     cache_metadata.CacheCondition.STALE_GEOMETRY),
])
def test_exact_fingerprint_change_invalidates_cache(tmp_path, fingerprint,
                                                    condition):
    path, _metadata = _publish(tmp_path)
    result = cache_metadata.inspect_cache(
        path, settings_fingerprint=fingerprint[0],
        geometry_fingerprint=fingerprint[1])
    assert result.condition is condition
    assert not result.usable


def test_same_size_pc2_tampering_is_detected_by_sha256(tmp_path):
    path, _metadata = _publish(tmp_path)
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    result = cache_metadata.inspect_cache(path)
    assert result.condition is cache_metadata.CacheCondition.CORRUPT
    assert "hash mismatch" in result.message


def test_truncated_pc2_is_detected_before_playback(tmp_path):
    path, _metadata = _publish(tmp_path)
    path.write_bytes(path.read_bytes()[:-4])
    result = cache_metadata.inspect_cache(path)
    assert result.condition is cache_metadata.CacheCondition.CORRUPT
    assert "size changed" in result.message


def test_sidecar_tampering_is_detected_by_metadata_digest(tmp_path):
    path, _metadata = _publish(tmp_path)
    sidecar = cache_metadata.sidecar_path(path)
    value = json.loads(sidecar.read_text(encoding="utf-8"))
    value["details"]["frame_end"] = 99
    sidecar.write_text(json.dumps(value), encoding="utf-8")
    result = cache_metadata.inspect_cache(path)
    assert result.condition is cache_metadata.CacheCondition.CORRUPT
    assert "metadata digest mismatch" in result.message


def test_complete_metadata_rejects_unexpected_pc2_layout(tmp_path):
    path = tmp_path / "cn_test_cloth_wrong.pc2"
    pc2.write_pc2(path, [((0, 0, 0),)])
    with pytest.raises(cache_metadata.CacheMetadataError,
                       match="vertex_count"):
        cache_metadata.completed_metadata(_partial(path), cache_path=path)


def test_writer_or_solver_update_does_not_mutate_or_invalidate_cache(
        tmp_path, monkeypatch):
    path, metadata = _publish(tmp_path)
    sidecar_before = cache_metadata.sidecar_path(path).read_bytes()
    cache_before = path.read_bytes()
    monkeypatch.setattr(pc2, "PC2_WRITER_VERSION",
                        metadata["pc2"]["writer_version"] + 1)

    result = cache_metadata.inspect_cache(path)

    assert result.condition is cache_metadata.CacheCondition.READY
    assert path.read_bytes() == cache_before
    assert cache_metadata.sidecar_path(path).read_bytes() == sidecar_before


def test_atomic_json_write_leaves_no_temporary_file(tmp_path):
    path = tmp_path / "cache.meta.json"
    cache_metadata.write_atomic(path, {"value": 1})
    cache_metadata.write_atomic(path, {"value": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 2}
    assert list(tmp_path.glob(".*.tmp")) == []
