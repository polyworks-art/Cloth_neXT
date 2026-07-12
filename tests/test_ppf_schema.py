# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Scene/Param schema encoding: exact fields, types, hashes, and goldens."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.schema import cbor_codec, envelope
from cloth_next.ppf.schema.data import (SceneEncodeError, SceneObject,
                                        build_scene_payload, encode_scene,
                                        zero_area_triangles)
from cloth_next.ppf.schema.params import (FIXED_SHELL_MATERIAL,
                                          ParamEncodeError,
                                          SimulationSettings,
                                          build_param_payload, encode_param,
                                          normalized_young_modulus)

FIXTURES = Path(__file__).parent / "fixtures" / "ppf_0_11"


def _micro_objects() -> tuple[SceneObject, SceneObject]:
    cloth = SceneObject(
        "MicroCloth", "cn-cloth-0001",
        ((-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (-0.5, 0.5, 0.0), (0.5, 0.5, 0.0)),
        ((0, 1, 3), (0, 3, 2)),
        solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0.8),
                             (0, 0, 0, 1))))
    collider = SceneObject(
        "MicroCollider", "cn-collider-0001",
        ((0.0, 0.0, 0.3), (0.3, 0.0, -0.3), (-0.15, 0.26, -0.3),
         (-0.15, -0.26, -0.3)),
        ((0, 1, 2), (0, 2, 3), (0, 3, 1), (1, 3, 2)),
        solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0.35),
                             (0, 0, 0, 1))))
    return cloth, collider


def _micro_settings() -> SimulationSettings:
    return SimulationSettings(frame_count=8, fps=24,
                              gravity_blender=(0.0, 0.0, -9.81))


def test_scene_payload_structure_and_types():
    cloth, collider = _micro_objects()
    payload = build_scene_payload(cloth, collider)
    assert [group["type"] for group in payload] == ["SHELL", "STATIC"]
    info = payload[0]["object"][0]
    assert set(info) == {"name", "uuid", "vert", "transform", "face"}
    assert info["name"] == "MicroCloth" and info["uuid"] == "cn-cloth-0001"
    assert all(isinstance(c, float) for v in info["vert"] for c in v)
    assert all(isinstance(i, int) for tri in info["face"] for i in tri)
    assert len(info["transform"]) == 4
    # transform = Z2Y @ world: Blender +Z translation lands in solver row 1.
    assert info["transform"][1] == [0.0, 0.0, 1.0, 0.8]
    assert info["transform"][2][1] == -1.0


def test_scene_golden_bytes_match_shipped_cbor2_output():
    cloth, collider = _micro_objects()
    blob, digest = encode_scene(cloth, collider)
    golden = (FIXTURES / "scene_micro.cbor").read_bytes()
    assert blob == golden
    meta = json.loads((FIXTURES / "phase3a_goldens.json").read_text())
    assert digest == meta["scene_micro_sha256"]
    assert hashlib.sha256(blob).hexdigest() == digest


def test_param_payload_structure_and_values():
    payload = build_param_payload(_micro_settings(), "MicroCloth",
                                  "cn-cloth-0001", "MicroCollider",
                                  "cn-collider-0001")
    scene = payload["scene"]
    assert scene["frames"] == 7  # Blender 1..8 -> solver 0..7
    assert scene["fps"] == 24
    assert scene["dt"] == 1e-3
    assert scene["gravity"] == [0.0, -9.81, -0.0]
    assert scene["friction-mode"] == "min"
    assert scene["disable-contact"] is False
    shell, shell_names, shell_uuids = payload["group"][0]
    assert shell == FIXED_SHELL_MATERIAL
    assert shell_names == ["MicroCloth"] and shell_uuids == ["cn-cloth-0001"]
    static, _names, static_uuids = payload["group"][1]
    assert set(static) == {"friction", "contact-gap", "contact-offset"}
    assert static_uuids == ["cn-collider-0001"]
    assert payload["pin_config"] == {}


def test_param_golden_bytes_match_shipped_cbor2_output():
    blob, digest = encode_param(_micro_settings(), "MicroCloth",
                                "cn-cloth-0001", "MicroCollider",
                                "cn-collider-0001")
    golden = (FIXTURES / "param_micro.cbor").read_bytes()
    assert blob == golden
    meta = json.loads((FIXTURES / "phase3a_goldens.json").read_text())
    assert digest == meta["param_micro_sha256"]


def test_envelope_metadata_and_rejections():
    blob = envelope.dumps_envelope(envelope.KIND_SCENE, [])
    decoded = cbor_codec.loads(blob)
    assert decoded == {"version": 1, "kind": "Scene", "payload": []}
    assert envelope.loads_envelope(blob, "Scene") == []
    with pytest.raises(envelope.EnvelopeError):
        envelope.loads_envelope(blob, "Param")
    wrong_version = cbor_codec.dumps({"version": 2, "kind": "Scene",
                                      "payload": []})
    with pytest.raises(envelope.EnvelopeError):
        envelope.loads_envelope(wrong_version, "Scene")
    missing_payload = cbor_codec.dumps({"version": 1, "kind": "Scene"})
    with pytest.raises(envelope.EnvelopeError):
        envelope.loads_envelope(missing_payload, "Scene")
    with pytest.raises(envelope.EnvelopeError):
        envelope.dumps_envelope("NotAKind", [])


def test_young_modulus_density_normalization():
    assert normalized_young_modulus(1000.0, 1000.0) == 1.0
    assert normalized_young_modulus(500.0, 250.0) == 2.0
    with pytest.raises(ParamEncodeError):
        normalized_young_modulus(1.0, 0.0)


def test_scene_object_validation():
    cloth, collider = _micro_objects()
    with pytest.raises(SceneEncodeError):
        SceneObject("", "u", cloth.vertices_local, cloth.triangles,
                    cloth.transform)
    with pytest.raises(SceneEncodeError):
        SceneObject("x", "u", (), cloth.triangles, cloth.transform)
    with pytest.raises(SceneEncodeError):
        SceneObject("x", "u", cloth.vertices_local, ((0, 1, 99),),
                    cloth.transform)
    with pytest.raises(SceneEncodeError):
        SceneObject("x", "u", cloth.vertices_local, ((0, 1, 1),),
                    cloth.transform)
    with pytest.raises(SceneEncodeError):
        SceneObject("x", "u", ((float("nan"), 0, 0),) * 3, ((0, 1, 2),),
                    cloth.transform)
    with pytest.raises(SceneEncodeError):
        build_scene_payload(cloth, SceneObject(
            "y", cloth.uuid, collider.vertices_local, collider.triangles,
            collider.transform))


def test_simulation_settings_validation():
    with pytest.raises(ParamEncodeError):
        SimulationSettings(frame_count=1, fps=24,
                           gravity_blender=(0, 0, -9.81))
    with pytest.raises(ParamEncodeError):
        SimulationSettings(frame_count=8, fps=0,
                           gravity_blender=(0, 0, -9.81))
    with pytest.raises(ParamEncodeError):
        SimulationSettings(frame_count=8, fps=24,
                           gravity_blender=(0, 0, float("inf")))


def test_zero_area_triangle_detection():
    vertices = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (2, 0, 0))
    assert zero_area_triangles(vertices, ((0, 1, 2),)) == []
    assert zero_area_triangles(vertices, ((0, 1, 3), (0, 1, 2))) == [0]
