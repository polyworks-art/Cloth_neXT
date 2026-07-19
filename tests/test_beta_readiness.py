from pathlib import Path

import pytest

from cloth_next.bake import cache_metadata, pc2
from cloth_next.core.beta_readiness import (
    CacheEntry, collider_capture_bytes, human_bytes, inventory_cache,
    pc2_size_bytes, redact_text, remove_invalid, support_markdown)


def test_storage_estimates_are_deterministic_and_include_endpoints():
    assert pc2_size_bytes(10, 3) == 32 + 10 * 3 * 12
    # Two Blender intervals at 8 samples plus the exact final endpoint.
    assert collider_capture_bytes(10, 3, 8) == 10 * 17 * 12
    assert human_bytes(1024 * 1024) == "1.0 MB"


def test_inventory_never_marks_legacy_cache_without_metadata_deletable(tmp_path):
    path = tmp_path / "cn_test_cloth_legacy.pc2"
    path.write_bytes(b"legacy")
    entries = inventory_cache(tmp_path)
    assert len(entries) == 1
    assert entries[0].condition == "MISSING"
    assert not entries[0].deletable


def test_partial_owned_cache_is_inventory_safe_and_removable(tmp_path):
    path = tmp_path / "cn_test_cloth_partial.pc2"
    path.write_bytes(b"partial")
    metadata = cache_metadata.partial_metadata(
        cache_path=path,
        fingerprints={"settings": "s", "geometry": "g", "combined": "c",
                      "topology": "t", "object": "o", "scene": "x"},
        identities={}, expected={}, details={})
    cache_metadata.write_atomic(cache_metadata.sidecar_path(path), metadata)
    entries = inventory_cache(tmp_path)
    assert entries[0].condition == "PARTIAL"
    assert entries[0].deletable
    removed = remove_invalid(entries, tmp_path)
    assert set(removed) == {path, cache_metadata.sidecar_path(path)}
    assert inventory_cache(tmp_path) == ()


def test_cleanup_rejects_paths_outside_selected_root(tmp_path):
    outside = tmp_path.parent / "cn_test_cloth_escape.pc2"
    entry = CacheEntry(outside, outside.with_suffix(".meta.json"),
                       "CORRUPT", "bad", 1, True)
    with pytest.raises(ValueError, match="unsafe"):
        remove_invalid((entry,), tmp_path)


def test_support_report_redacts_longest_sensitive_values_and_has_no_payload():
    text = redact_text(
        r"C:\Users\Alice\Project\shot.blend and Demo_Cloth",
        {r"C:\Users\Alice": "<HOME>", "Demo_Cloth": "<OBJECT-1>"})
    assert "Alice" not in text and "Demo_Cloth" not in text
    report = support_markdown((("Privacy", (("Geometry included", "no"),)),))
    assert "Geometry included: no" in report
    assert "mesh geometry or file contents" in report
