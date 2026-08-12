# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import replace
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.materials import DEFAULT_STATIC_SETTINGS, ShellMaterialSettings
from cloth_next.materials.deformables import (
    DeformableMaterialError, RigidBodyMaterialSettings, RodMaterialSettings,
    SoftBodyMaterialSettings)
from cloth_next.ppf.schema import envelope
from cloth_next.ppf.schema.data import (
    GROUP_PDRD, GROUP_ROD, GROUP_SHELL, GROUP_SOLID, SceneObject,
    build_deformable_scene_payload, build_multi_deformable_scene_payload,
    encode_deformable_scene)
from cloth_next.ppf.schema.params import (
    SimulationSettings, build_deformable_param_payload,
    build_multi_deformable_param_payload)
from cloth_next.curve_rod import CurveRodError, sample_curve
from cloth_next.pinning import StaticPinConfig

IDENTITY = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
COLLIDER = SceneObject("floor", "static-1",
    ((0, 0, 0), (1, 0, 0), (0, 1, 0)), ((0, 1, 2),), IDENTITY)
STATIC_SPEC = (("floor", "static-1", DEFAULT_STATIC_SETTINGS),)
SETTINGS = SimulationSettings(3, 24, (0.0, 0.0, -9.81))


def test_cable_rope_collision_radius_is_visible_and_documented():
    root = Path(__file__).resolve().parents[1]
    ui = (root / "cloth_next/blender/physics_ui.py").read_text("utf-8")
    readme = (root / "README.md").read_text("utf-8")
    assert '"surface_offset",' in ui
    assert 'text="Collision Radius"' in ui
    assert "one-dimensional centerline" in readme


def test_rod_scene_uses_edges_without_faces():
    rod = SceneObject("cable", "rod-1", ((0, 0, 1), (0, 0, 2)), (),
                      IDENTITY, edges=((0, 1),))
    payload = build_deformable_scene_payload(rod, COLLIDER,
                                             group_type=GROUP_ROD)
    assert payload[0]["type"] == "ROD"
    assert payload[0]["object"][0]["edge"] == [[0, 1]]
    assert "face" not in payload[0]["object"][0]
    blob, _digest = encode_deformable_scene(rod, COLLIDER,
                                            group_type=GROUP_ROD)
    assert envelope.loads_envelope(blob, envelope.KIND_SCENE) == payload


def test_soft_body_scene_and_parameters_request_tetrahedralization():
    solid = SceneObject("soft", "solid-1",
        ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)), IDENTITY)
    scene = build_deformable_scene_payload(solid, COLLIDER,
                                           group_type=GROUP_SOLID)
    assert scene[0]["type"] == "SOLID" and "face" in scene[0]["object"][0]
    material = SoftBodyMaterialSettings(
        tetrahedralizer="tetgen", stretch_plasticity_rate=2.0,
        stretch_plasticity_threshold_percent=4.0)
    params = build_deformable_param_payload(
        SETTINGS, "soft", "solid-1", STATIC_SPEC, group_type="SOLID",
        material=material)
    assert params["group"][0][0]["model"] == "arap"
    assert params["group"][0][0]["ftetwild"] == {
        "solid-1": {"backend": "tetgen"}}
    assert params["group"][0][0]["plasticity"] == pytest.approx(2.0)
    assert params["group"][0][0]["plasticity-threshold"] == pytest.approx(0.04)


def test_rod_parameters_and_material_validation():
    rod = RodMaterialSettings(
        length_factor=0.8, stretch_limit=0.05,
        bend_plasticity_rate=1.5, bend_plasticity_threshold_degrees=45.0)
    params = build_deformable_param_payload(
        SETTINGS, "cable", "rod-1", STATIC_SPEC, group_type="ROD",
        material=rod)
    wire = params["group"][0][0]
    assert wire["model"] == "arap"
    assert wire["length-factor"] == pytest.approx(0.8)
    assert wire["strain-limit"] == pytest.approx(0.05)
    assert wire["bend-plasticity"] == pytest.approx(1.5)
    assert wire["bend-plasticity-threshold"] == pytest.approx(math.pi / 4.0)
    with pytest.raises(DeformableMaterialError):
        replace(rod, linear_density=0.0)


def test_rigid_body_uses_pdrd_with_only_mass_and_contact_controls():
    rigid = SceneObject("box", "rigid-1",
        ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)), IDENTITY)
    scene = build_deformable_scene_payload(
        rigid, COLLIDER, group_type=GROUP_PDRD)
    assert scene[0]["type"] == "PDRD"
    params = build_deformable_param_payload(
        SETTINGS, "box", "rigid-1", STATIC_SPEC, group_type=GROUP_PDRD,
        material=RigidBodyMaterialSettings(volume_density=250.0))
    wire = params["group"][0][0]
    assert wire == {"model": "pdrd", "density": 250.0, "friction": 0.25,
                    "contact-gap": pytest.approx(0.001),
                    "contact-offset": 0.0}
    with pytest.raises(DeformableMaterialError):
        replace(RigidBodyMaterialSettings(), volume_density=0.0)


def test_mixed_deformables_share_one_scene_and_keep_own_materials():
    cloth = SceneObject("cloth", "shell-1",
        ((0, 0, 1), (1, 0, 1), (0, 1, 1)), ((0, 1, 2),), IDENTITY)
    rod = SceneObject("cable", "rod-1", ((0, 0, 2), (0, 0, 3)), (),
                      IDENTITY, edges=((0, 1),))
    scene = build_multi_deformable_scene_payload(
        ((cloth, GROUP_SHELL), (rod, GROUP_ROD)), COLLIDER)
    assert [group["type"] for group in scene] == ["SHELL", "ROD", "STATIC"]
    assert [group["object"][0]["uuid"] for group in scene] == [
        "shell-1", "rod-1", "static-1"]
    params = build_multi_deformable_param_payload(
        SETTINGS,
        (("cloth", "shell-1", "SHELL", ShellMaterialSettings(), None),
         ("cable", "rod-1", "ROD", RodMaterialSettings(), None)),
        STATIC_SPEC)
    assert [group[2][0] for group in params["group"]] == [
        "shell-1", "rod-1", "static-1"]
    assert params["group"][0][0]["model"] == "baraff-witkin"
    assert params["group"][1][0]["model"] == "arap"


def test_multi_deformables_keep_separate_animated_pin_tracks():
    pins_a = StaticPinConfig((0,), pin_group_id="pins-a", times=(0.0, 1.0),
        positions=(((0.0, 0.0, 0.0),), ((0.0, 0.0, 1.0),)))
    pins_b = StaticPinConfig((1,), pin_group_id="pins-b", times=(0.0, 1.0),
        positions=(((2.0, 0.0, 0.0),), ((2.0, 1.0, 0.0),)))
    payload = build_multi_deformable_param_payload(
        SETTINGS,
        (("a", "uuid-a", "SHELL", ShellMaterialSettings(), pins_a),
         ("b", "uuid-b", "SHELL", ShellMaterialSettings(), pins_b)),
        STATIC_SPEC)
    assert set(payload["pin_config"]) == {"uuid-a", "uuid-b"}
    assert payload["pin_config"]["uuid-a"][0]["pin_group_id"] == "pins-a"
    assert payload["pin_config"]["uuid-b"][1]["pin_group_id"] == "pins-b"
    assert payload["pin_config"]["uuid-a"][0]["pin_anim"][0]["position"][-1] == [0.0, 0.0, 1.0]


def test_poly_curve_sampling_preserves_splines_and_cyclic_edges():
    def point(co):
        return SimpleNamespace(co=(*co, 1.0))

    open_spline = SimpleNamespace(type="POLY", points=[point((0, 0, 0)),
        point((1, 0, 0)), point((2, 0, 0))], use_cyclic_u=False)
    loop = SimpleNamespace(type="POLY", points=[point((0, 1, 0)),
        point((1, 1, 0))], use_cyclic_u=True)
    obj = SimpleNamespace(name="Cable", data=SimpleNamespace(
        splines=[open_spline, loop]))
    vertices, edges, metadata = sample_curve(obj)
    assert len(vertices) == 5
    assert edges == ((0, 1), (1, 2), (3, 4), (4, 3))
    assert metadata == (("POLY", 3, False), ("POLY", 2, True))


def test_nurbs_rod_has_actionable_conversion_error():
    obj = SimpleNamespace(name="Cable", data=SimpleNamespace(splines=[
        SimpleNamespace(type="NURBS")]))
    with pytest.raises(CurveRodError, match="convert.*Bezier or Poly"):
        sample_curve(obj)


def test_smoke_tool_threads_resolved_schema_version(monkeypatch, tmp_path):
    from tools import run_ppf_rod_softbody as smoke

    captured: dict[str, tuple[int, str]] = {}

    class _FakeResolver:
        def __init__(self, _probe):
            pass

        def resolve(self, context):
            return SimpleNamespace(schema_version="2", protocol_version="0.18")

    def _fake_run(resolved, output, kind, *, schema_version,
                  protocol_version):
        captured[kind] = (schema_version, protocol_version)
        return {"frames": 4, "vertices": 1}

    monkeypatch.setattr(smoke, "SolverResolver", _FakeResolver)
    monkeypatch.setattr(smoke, "_run", _fake_run)
    report = smoke.run(Path("ignored-solver"), tmp_path)
    assert report["result"] == "PASS"
    assert captured == {"ROD": (2, "0.18"), "SOLID": (2, "0.18"),
                        "PDRD": (2, "0.18")}


def test_smoke_payloads_carry_resolved_schema_envelope():
    from tools.run_ppf_rod_softbody import _encode_payloads

    solid = SceneObject("soft", "solid-1",
        ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)), IDENTITY)
    settings, data, _data_hash, params, _param_hash = _encode_payloads(
        solid, COLLIDER, "SOLID", SoftBodyMaterialSettings(), schema_version=2)
    assert settings.frame_count == 5
    scene = envelope.loads_envelope(data, envelope.KIND_SCENE,
                                    schema_version=2)
    assert scene[0]["type"] == "SOLID"
    param = envelope.loads_envelope(params, envelope.KIND_PARAM,
                                    schema_version=2)
    assert param["group"][0][0]["model"] == "arap"
    with pytest.raises(envelope.EnvelopeError, match="schema version mismatch"):
        envelope.loads_envelope(data, envelope.KIND_SCENE, schema_version=1)
