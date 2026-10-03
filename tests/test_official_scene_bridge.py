from dataclasses import dataclass, replace
import json

import numpy as np
import pytest

from cloth_next.ppf.official_scene_bridge import (
    bind_face_friction, bind_stiffness, create_owned_project_link, prepare_scene, recovery_param_hash)
from cloth_next.ppf.schema import envelope


@dataclass(frozen=True)
class InputScene:
    data_payload: bytes
    param_payload: bytes
    data_hash: str
    param_hash: str
    official_bridge_json: str = ""
    water_stream_json: str = ""


def test_water_fingerprint_and_time_offset_gate_existing_recovery_identity():
    original=scene()
    one=replace(original,water_stream_json=json.dumps({'first':120,'fingerprint':'one'}))
    two=replace(original,water_stream_json=json.dumps({'first':120,'fingerprint':'two'}))
    shifted=replace(original,water_stream_json=json.dumps({'first':121,'fingerprint':'one'}))
    assert len({recovery_param_hash(v) for v in (original,one,two,shifted)})==4


def scene(strengths=(100.0, 450.0), *, legacy=False):
    obj = {"uuid": "cloth", "name": "cloth", "vert": [[0, 0, 0]]*16,
           "face": [[0, 1, 2], [1, 2, 3]], "face_friction": [0.2, 0.9]}
    if legacy:
        obj["stitch"] = [[[1, 4, 4, 4]], [[1, 1, 0, 0]]]
    rows = []
    for a, b, strength in ((1, 4, strengths[0]), (9, 12, strengths[1])):
        rows.append({"source_uuid": "cloth", "target_uuid": "cloth",
                     "ind": [[a, a, a, b, b, b]],
                     "w": [[1.0, 0, 0, 1.0, 0, 0]],
                     "stitch_stiffness": strength})
    rows.append({"source_uuid": "cloth", "target_uuid": "other",
                 "ind": [[1, 1, 1, 0, 0, 0]],
                 "w": [[1.0, 0, 0, 1.0, 0, 0]], "stitch_stiffness": 300.0})
    data = envelope.dumps_envelope("Scene", [{"type": "SHELL", "object": [obj]}],
                                   schema_version=2)
    params = envelope.dumps_envelope("Param", {"cross_stitch": rows}, schema_version=2)
    return InputScene(data, params, envelope.payload_sha256(data),
                      envelope.payload_sha256(params))


def test_official_bridge_preserves_original_artist_input_and_cross_contract():
    original = scene(legacy=True)
    result = prepare_scene(original, enabled=True)
    data = envelope.loads_envelope(result.data_payload, "Scene", schema_version=2)
    params = envelope.loads_envelope(result.param_payload, "Param", schema_version=2)
    obj = data[0]["object"][0]
    assert obj["uuid"] == "cloth" and len(obj["vert"]) == 16
    assert obj["stitch"][0] == [[1, 4, 4, 4], [1, 4, 4, 4], [9, 12, 12, 12]]
    assert "face_friction" not in obj
    assert len(params["cross_stitch"]) == 1
    assert params["cross_stitch"][0]["target_uuid"] == "other"
    document = json.loads(result.official_bridge_json)
    assert [row["stiffness"] for row in document["stitches"]] == [100, 450]
    assert document["face_friction"][0]["values"] == [0.2, 0.9]
    assert prepare_scene(result, enabled=True) is result
    assert result == prepare_scene(original, enabled=True)
    assert original.official_bridge_json == ""
    assert len(envelope.loads_envelope(original.param_payload, "Param", schema_version=2)
               ["cross_stitch"]) == 3


def test_old_generation_is_byte_identical():
    original = scene()
    assert prepare_scene(original, enabled=False) is original
    assert recovery_param_hash(original) == original.param_hash


def test_standalone_worker_bootstrap_preserves_extension_namespace():
    import importlib
    worker = importlib.import_module("cloth_next.ppf.official_scene_worker")
    assert callable(worker.bind_stiffness) and callable(worker.bind_face_friction)


def test_file_backed_blender_payloads_are_decoded_as_content(tmp_path):
    from dataclasses import replace
    original = scene()
    data, param = tmp_path / "data.cbor", tmp_path / "param.cbor"
    data.write_bytes(original.data_payload)
    param.write_bytes(original.param_payload)
    staged = replace(original, data_payload=data, param_payload=param)
    assert prepare_scene(staged, enabled=True) == prepare_scene(original, enabled=True)
    assert data.read_bytes() == original.data_payload
    assert param.read_bytes() == original.param_payload


def test_owned_project_link_preserves_recovery_boundary_and_foreign_data(tmp_path):
    name = "clothnext_link_test"
    canonical = tmp_path / "official-data" / name
    owned = tmp_path / "cnx-run" / "server-data" / name
    canonical.parent.mkdir(parents=True)
    create_owned_project_link(canonical, owned, name)
    (canonical / ".cash").mkdir()
    assert canonical.resolve() == owned.resolve()
    assert (owned / ".cash").is_dir()
    create_owned_project_link(canonical, owned, name)
    other = tmp_path / "foreign" / name
    other.mkdir(parents=True)
    (other / "user-data").write_bytes(b"preserve")
    from cloth_next.core.errors import ClothNextError
    with pytest.raises(ClothNextError):
        create_owned_project_link(other, owned, name)
    assert (other / "user-data").read_bytes() == b"preserve"


def test_both_seam_strengths_invalidate_resume_identity():
    one = prepare_scene(scene(), enabled=True)
    two = prepare_scene(scene((100, 451)), enabled=True)
    # Native PARAM no longer contains intra strengths, but the owned bridge
    # identity must still reject recovery after changing the second seam.
    assert one.param_hash == two.param_hash
    assert recovery_param_hash(one) != recovery_param_hash(two)


def test_row_binding_uses_vertex_map_and_preserves_legacy_duplicates():
    prepared = prepare_scene(scene(legacy=True), enabled=True)
    bindings = json.loads(prepared.official_bridge_json)["stitches"]
    mapping = {"cloth": list(reversed(range(16)))}
    local = [[1, 1, 1, 4, 4, 4], [1, 1, 1, 4, 4, 4], [9, 9, 9, 12, 12, 12]]
    ind = [[mapping["cloth"][i] for i in row] for row in local]
    weights = [[1, 0, 0, 1, 0, 0]]*3
    resolved = bind_stiffness(ind, weights, [7, 7, 7], mapping, bindings)
    np.testing.assert_array_equal(resolved, [7, 100, 450])
    with pytest.raises(ValueError, match="did not retain"):
        bind_stiffness(ind[:1], weights[:1], [7], mapping, bindings)
    with pytest.raises(ValueError, match="missing vertex map"):
        bind_stiffness(ind, weights, [7]*3, {}, bindings)


def test_triangle_friction_uses_actual_official_order_not_object_offsets():
    rows = [{"uuid": "cloth", "faces": [[0, 1, 2], [1, 2, 3]],
             "values": [0.2, 0.9]}]
    resolved = bind_face_friction([[11, 12, 13], [20, 21, 22], [10, 11, 12]],
        [7, 8, 9], {"cloth": [10, 11, 12, 13]}, rows)
    np.testing.assert_array_equal(resolved, np.asarray([0.9, 8, 0.2], dtype=np.float32))
    with pytest.raises(ValueError, match="painted triangle"):
        bind_face_friction([[10, 11, 12]], [1], {"cloth": [10, 11, 12, 13]}, rows)


@pytest.mark.parametrize("field,value", [
    ("source_uuid", "missing"), ("ind", [[17, 17, 17, 4, 4, 4]]),
    ("w", [[0.5, 0.5, 0, 1, 0, 0]]), ("stitch_stiffness", -1),
])
def test_invalid_same_object_seam_is_not_dropped(field, value):
    original = scene()
    params = envelope.loads_envelope(original.param_payload, "Param", schema_version=2)
    params["cross_stitch"][0][field] = value
    if field == "source_uuid":
        params["cross_stitch"][0]["target_uuid"] = value
    changed = replace(original, param_payload=envelope.dumps_envelope(
        "Param", params, schema_version=2))
    with pytest.raises(ValueError):
        prepare_scene(changed, enabled=True)
