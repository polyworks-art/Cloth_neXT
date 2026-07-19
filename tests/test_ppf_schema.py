# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Scene/Param schema encoding: exact fields, types, hashes, and goldens."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from cloth_next.materials import (
    DEFAULT_SHELL_SETTINGS,
    DEFAULT_STATIC_SETTINGS,
    MaterialValidationError,
    ShellMaterialSettings,
    StaticMaterialSettings,
)
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.schema import cbor_codec, envelope
from cloth_next.ppf.schema.data import (
    SceneEncodeError,
    SceneObject,
    build_scene_payload,
    encode_scene,
    zero_area_triangles,
)
from cloth_next.ppf.schema.params import (
    ParamEncodeError,
    SimulationSettings,
    build_multi_collider_param_payload,
    build_param_payload,
    encode_param,
    float32_wire,
    shell_wire_params,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ppf_0_11"

SHELL_KEYS = ["model", "density", "young-mod", "poiss-rat", "bend",
              "deformation-damping", "bending-damping", "friction",
              "contact-gap", "contact-offset", "strain-limit", "pressure"]
STATIC_KEYS = ["friction", "contact-gap", "contact-offset"]


def _micro_payload(**kwargs):
    defaults = dict(shell=DEFAULT_SHELL_SETTINGS,
                    static=DEFAULT_STATIC_SETTINGS, contact_enabled=True)
    defaults.update(kwargs)
    return build_param_payload(_micro_settings(), "MicroCloth",
                               "cn-cloth-0001", "MicroCollider",
                               "cn-collider-0001", **defaults)


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


def test_scene_and_params_allow_no_collider():
    cloth,_collider=_micro_objects()
    scene=build_scene_payload(cloth,())
    assert [group["type"] for group in scene]==["SHELL"]
    params=build_multi_collider_param_payload(
        _micro_settings(),cloth.name,cloth.uuid,(),
        shell=DEFAULT_SHELL_SETTINGS)
    assert len(params["group"])==1
    assert params["group"][0][2]==[cloth.uuid]


def test_official_static_deform_animation_roundtrip():
    cloth, collider = _micro_objects()
    frames = np.asarray([collider.vertices_local, collider.vertices_local],
                        dtype=np.float32)
    animated = SceneObject(
        collider.name, collider.uuid, collider.vertices_local,
        collider.triangles, collider.transform,
        static_deform_animation={"time": [0.0, 1.0 / 24.0],
                                 "vert_frames": frames})
    blob, _digest = encode_scene(cloth, animated)
    payload = envelope.loads_envelope(blob, envelope.KIND_SCENE)
    motion = payload[1]["object"][0]["static_deform_animation"]
    assert motion["time"] == [0.0, 1.0 / 24.0]
    assert motion["vert_frames"] == frames.tolist()


def test_multiple_colliders_keep_deterministic_scene_and_param_order():
    cloth, collider = _micro_objects()
    second = SceneObject("Second", "cn-collider-0002",
                         collider.vertices_local, collider.triangles,
                         collider.transform)
    payload = build_scene_payload(cloth, (collider, second))
    assert [item["uuid"] for item in payload[1]["object"]] == [
        "cn-collider-0001", "cn-collider-0002"]
    params = build_multi_collider_param_payload(
        _micro_settings(), cloth.name, cloth.uuid,
        ((collider.name, collider.uuid, DEFAULT_STATIC_SETTINGS),
         (second.name, second.uuid, DEFAULT_STATIC_SETTINGS)),
        shell=DEFAULT_SHELL_SETTINGS)
    assert [entry[2][0] for entry in params["group"][1:]] == [
        "cn-collider-0001", "cn-collider-0002"]


def test_scene_golden_bytes_match_shipped_cbor2_output():
    cloth, collider = _micro_objects()
    blob, digest = encode_scene(cloth, collider)
    golden = (FIXTURES / "scene_micro.cbor").read_bytes()
    assert blob == golden
    meta = json.loads((FIXTURES / "phase3a_goldens.json").read_text())
    assert digest == meta["scene_micro_sha256"]
    assert hashlib.sha256(blob).hexdigest() == digest


def test_param_payload_structure_and_values():
    payload = _micro_payload()
    scene = payload["scene"]
    assert scene["frames"] == 7  # Blender 1..8 -> solver 0..7
    assert scene["fps"] == 24
    assert scene["dt"] == float32_wire(1e-3)
    assert scene["min-newton-steps"] == 1
    assert scene["cg-max-iter"] == 10000
    assert scene["cg-tol"] == float32_wire(0.001)
    assert scene["gravity"] == [0.0, -9.81, -0.0]
    assert scene["wind"] == [0.0, 0.0, 0.0]
    assert scene["friction-mode"] == "min"
    assert scene["disable-contact"] is False
    shell, shell_names, shell_uuids = payload["group"][0]
    assert list(shell) == SHELL_KEYS  # exactly the audited keys, in order
    assert shell["model"] == "baraff-witkin"
    assert shell["density"] == 1.0 and shell["young-mod"] == 1000.0
    assert shell_names == ["MicroCloth"] and shell_uuids == ["cn-cloth-0001"]
    static, _names, static_uuids = payload["group"][1]
    assert list(static) == STATIC_KEYS
    assert static_uuids == ["cn-collider-0001"]
    assert payload["pin_config"] == {}


def test_wind_vector_is_encoded_in_ppf_coordinates():
    settings = SimulationSettings(
        frame_count=8, fps=24, gravity_blender=(0.0, 0.0, -9.81),
        wind_blender=(1.0, 2.0, 3.0))
    payload = build_param_payload(
        settings, "Cloth", "cloth", "Collider", "collider",
        shell=DEFAULT_SHELL_SETTINGS, static=DEFAULT_STATIC_SETTINGS)
    assert payload["scene"]["wind"] == [1.0, 3.0, -2.0]


def test_all_ppf_force_fields_and_native_animation_tracks_are_encoded():
    settings = SimulationSettings(
        frame_count=3, fps=20, gravity_blender=(0.0, 0.0, -9.81),
        wind_blender=(0.0, 0.0, 1.0), air_density=1.2,
        air_friction=0.3, vertex_air_damp=0.15,
        dynamic_parameters=(
            ("wind", ((0.0, (0.0, 0.0, 1.0), False),
                      (0.05, (0.0, 0.0, 2.0), False))),
            ("air-density", ((0.0, (1.2,), False),
                             (0.05, (0.8,), False))),))
    payload = build_param_payload(
        settings, "Cloth", "cloth", "Collider", "collider",
        shell=DEFAULT_SHELL_SETTINGS, static=DEFAULT_STATIC_SETTINGS)
    assert payload["scene"]["air-density"] == pytest.approx(1.2)
    assert payload["scene"]["air-friction"] == pytest.approx(0.3)
    assert payload["scene"]["isotropic-air-friction"] == pytest.approx(0.15)
    assert payload["dyn_param"]["wind"] == [
        (0.0, [0.0, 1.0, -0.0], False),
        (0.05, [0.0, 2.0, -0.0], False)]
    assert payload["dyn_param"]["air-density"][1][1][0] == pytest.approx(0.8)


def test_param_golden_bytes_match_shipped_cbor2_output():
    blob, digest = encode_param(_micro_settings(), "MicroCloth",
                                "cn-cloth-0001", "MicroCollider",
                                "cn-collider-0001",
                                shell=DEFAULT_SHELL_SETTINGS,
                                static=DEFAULT_STATIC_SETTINGS)
    golden = (FIXTURES / "param_micro.cbor").read_bytes()
    assert blob == golden
    meta = json.loads((FIXTURES / "phase3a_goldens.json").read_text())
    assert digest == meta["param_micro_sha256"]


# ---------------------------------------------------------------------------
# Phase-3B exact material mapping (task section 17)

def test_shell_artist_names_map_to_exact_wire_keys():
    shell = ShellMaterialSettings(
        surface_weight=2.5, stretch_resistance=5500.0,
        sideways_response=0.25, bend_resistance=4.3,
        shape_damping=0.01, fold_damping=0.002,
        surface_grip=0.35, collision_gap=0.004, surface_offset=0.002,
        stretch_limit_enabled=True, maximum_stretch_percent=5.0)
    wire = shell_wire_params(shell)
    assert wire["density"] == float32_wire(2.5)          # Surface Weight
    assert wire["young-mod"] == float32_wire(5500.0)     # Stretch Resistance
    assert wire["poiss-rat"] == float32_wire(0.25)       # Sideways Response
    assert wire["bend"] == float32_wire(4.3)             # Bend Resistance
    assert wire["deformation-damping"] == float32_wire(0.01)  # Shape Damping
    assert wire["bending-damping"] == float32_wire(0.002)     # Fold Damping
    assert wire["friction"] == float32_wire(0.35)        # Friction
    assert wire["contact-gap"] == float32_wire(0.004)    # Collision Gap
    assert wire["contact-offset"] == float32_wire(0.002)  # Surface Offset
    assert wire["strain-limit"] == float32_wire(0.05)    # 5% -> 0.05


def test_stretch_resistance_is_never_divided_by_density():
    # A density of 2.0 with stretch resistance 5500 must still emit
    # young-mod 5500 — NOT 2750. The presets store the already
    # density-normalized wire value; normalizing again is the regression
    # this test exists to prevent.
    shell = ShellMaterialSettings(surface_weight=2.0,
                                  stretch_resistance=5500.0)
    assert shell_wire_params(shell)["young-mod"] == 5500.0
    heavy = ShellMaterialSettings(surface_weight=1000.0,
                                  stretch_resistance=500.0)
    assert shell_wire_params(heavy)["young-mod"] == 500.0


def test_no_double_normalization_helper_exists():
    # The Phase-3A normalized_young_modulus() helper could silently
    # re-normalize an already-normalized preset; it must stay deleted.
    import cloth_next.ppf.schema.params as params_module
    assert not hasattr(params_module, "normalized_young_modulus")


def test_model_names_map_exactly_to_accepted_wire_values():
    fabric = ShellMaterialSettings(model="FABRIC")
    arap = ShellMaterialSettings(model="SHAPE_PRESERVING")
    assert shell_wire_params(fabric)["model"] == "baraff-witkin"
    assert shell_wire_params(arap)["model"] == "arap"
    with pytest.raises(MaterialValidationError):
        ShellMaterialSettings(model="STABLE_NEOHOOKEAN")


def test_strain_limit_percent_conversion_and_disable():
    limited = ShellMaterialSettings(stretch_limit_enabled=True,
                                    maximum_stretch_percent=5.0)
    assert shell_wire_params(limited)["strain-limit"] == float32_wire(0.05)
    disabled = ShellMaterialSettings(stretch_limit_enabled=False,
                                     maximum_stretch_percent=5.0)
    assert shell_wire_params(disabled)["strain-limit"] == 0.0


def test_collider_grip_gap_offset_map_independently_of_cloth():
    shell = ShellMaterialSettings(surface_grip=0.1, collision_gap=0.002,
                                  surface_offset=0.001)
    static = StaticMaterialSettings(surface_grip=0.9, collision_gap=0.005,
                                    surface_offset=0.003)
    payload = _micro_payload(shell=shell, static=static)
    shell_wire = payload["group"][0][0]
    static_wire = payload["group"][1][0]
    assert shell_wire["friction"] == float32_wire(0.1)
    assert static_wire["friction"] == float32_wire(0.9)
    assert shell_wire["contact-gap"] == float32_wire(0.002)
    assert static_wire["contact-gap"] == float32_wire(0.005)
    assert shell_wire["contact-offset"] == float32_wire(0.001)
    assert static_wire["contact-offset"] == float32_wire(0.003)


def test_contact_enable_maps_to_scene_disable_contact():
    assert _micro_payload(contact_enabled=True)["scene"][
        "disable-contact"] is False
    assert _micro_payload(contact_enabled=False)["scene"][
        "disable-contact"] is True


def test_no_unsupported_ui_property_enters_the_payload():
    payload = _micro_payload()
    assert list(payload["group"][0][0]) == SHELL_KEYS
    assert list(payload["group"][1][0]) == STATIC_KEYS
    for forbidden in ("stretch", "shear", "thickness",
                      "velocity", "self-collision", "stitch-stiffness",
                      "shrink", "plasticity"):
        assert forbidden not in payload["group"][0][0]
        assert forbidden not in payload["group"][1][0]


def test_all_encoded_numeric_values_are_finite_floats():
    import math as _math
    payload = _micro_payload(
        shell=ShellMaterialSettings(stretch_resistance=13000.0,
                                    bend_resistance=1.8,
                                    stretch_limit_enabled=True,
                                    maximum_stretch_percent=2.0))
    for group, _names, _uuids in payload["group"]:
        for key, value in group.items():
            if key == "model":
                continue
            assert isinstance(value, float), (key, value)
            assert _math.isfinite(value), (key, value)


def test_invalid_ranges_fail_before_encoding():
    with pytest.raises(MaterialValidationError):
        ShellMaterialSettings(surface_weight=0.0)  # must be > 0
    with pytest.raises(MaterialValidationError):
        ShellMaterialSettings(sideways_response=0.5)  # > 0.4999
    with pytest.raises(MaterialValidationError):
        ShellMaterialSettings(surface_grip=1.5)
    with pytest.raises(MaterialValidationError):
        ShellMaterialSettings(stretch_resistance=float("nan"))
    with pytest.raises(MaterialValidationError):
        ShellMaterialSettings(maximum_stretch_percent=0.0)
    with pytest.raises(MaterialValidationError):
        StaticMaterialSettings(surface_grip=-0.1)
    with pytest.raises(MaterialValidationError):
        StaticMaterialSettings(collision_gap=float("inf"))


def test_validation_error_names_property_value_range_and_action():
    with pytest.raises(MaterialValidationError) as excinfo:
        ShellMaterialSettings(sideways_response=0.75)
    error = excinfo.value
    assert error.property_name == "sideways_response"
    assert error.value == 0.75
    assert "0.4999" in error.accepted
    assert "Material" in error.action
    assert "sideways_response" in str(error) and "0.4999" in str(error)


def test_payload_hashes_are_deterministic():
    kwargs = dict(shell=ShellMaterialSettings(stretch_resistance=5500.0),
                  static=DEFAULT_STATIC_SETTINGS, contact_enabled=True)
    first = encode_param(_micro_settings(), "MicroCloth", "cn-cloth-0001",
                         "MicroCollider", "cn-collider-0001", **kwargs)
    second = encode_param(_micro_settings(), "MicroCloth", "cn-cloth-0001",
                          "MicroCollider", "cn-collider-0001", **kwargs)
    assert first == second
    different = encode_param(_micro_settings(), "MicroCloth",
                             "cn-cloth-0001", "MicroCollider",
                             "cn-collider-0001",
                             shell=ShellMaterialSettings(
                                 stretch_resistance=10000.0),
                             static=DEFAULT_STATIC_SETTINGS)
    assert different[1] != first[1]


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
