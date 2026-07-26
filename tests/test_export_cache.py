import json

from cloth_next.export_cache import ExportPayloadCache, deterministic_key


def test_scene_and_param_keys_invalidate_independently():
    scene = deterministic_key("scene", {"geometry": "a", "uuid": "u"})
    quality_change = deterministic_key(
        "scene", {"geometry": "a", "uuid": "u"})
    assert scene == quality_change
    assert deterministic_key("param", {"quality": "low"}) != (
        deterministic_key("param", {"quality": "high"}))


def test_cache_store_lookup_and_corruption(tmp_path):
    cache = ExportPayloadCache(tmp_path)
    key = deterministic_key("scene", {"geometry": "abc"})
    stored = cache.store("scene", key, b"solver payload")
    assert stored.hit
    lookup = cache.lookup("scene", key)
    assert lookup.hit and lookup.path.read_bytes() == b"solver payload"
    lookup.path.write_bytes(b"damaged")
    assert cache.lookup("scene", key).reason in {"size mismatch", "hash mismatch"}


def test_cache_rejects_incomplete_metadata(tmp_path):
    cache = ExportPayloadCache(tmp_path)
    key = deterministic_key("param", {"quality": 1})
    payload, metadata = cache._paths("param", key)
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"x")
    metadata.write_text(json.dumps({"kind": "param"}), encoding="utf-8")
    assert not cache.lookup("param", key).hit


def test_plan_artifacts_are_hash_verified(tmp_path):
    cache = ExportPayloadCache(tmp_path)
    key = deterministic_key("scene", {"geometry": "plan"})
    stored = cache.store(
        "scene", key, b"scene", plan={"version": 1},
        artifacts={"initial_000.f32": b"\x00" * 12})
    assert stored.metadata == {"version": 1}
    artifacts = cache.lookup_artifacts("scene", key)
    assert artifacts["initial_000.f32"].read_bytes() == b"\x00" * 12
    artifacts["initial_000.f32"].write_bytes(b"broken")
    assert cache.lookup_artifacts("scene", key) == {}
