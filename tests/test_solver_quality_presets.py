# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cloth_next.materials import DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS
from cloth_next.materials.formatting import settings_fingerprint
from cloth_next.ppf.schema.params import (SimulationSettings,
                                          build_param_payload, float32_wire)
from cloth_next.solver_quality import (
    DEFAULT_SOLVER_QUALITY, QUALITY_FLOAT_ABS_TOLERANCE, QUALITY_PRESETS,
    SolverQualitySettings, SolverQualityValidationError, apply_quality_preset,
    matching_quality_preset, quality_preset)
from tests import fake_bpy


EXPECTED = {
    "LOW": (0.010, 1, 2500, 0.010),
    "MEDIUM": (0.005, 1, 5000, 0.005),
    "HIGH": (0.001, 1, 10000, 0.001),
    "EXTREME": (0.001, 4, 25000, 0.0001),
}


def values(settings):
    return (settings.time_step, settings.min_newton_steps,
            settings.cg_max_iter, settings.cg_tol)


def test_preset_catalog_values_validation_and_high_default():
    assert [preset.identifier for preset in QUALITY_PRESETS] == list(EXPECTED)
    assert len({preset.identifier for preset in QUALITY_PRESETS}) == 4
    for preset in QUALITY_PRESETS:
        assert values(preset.settings) == EXPECTED[preset.identifier]
        assert SolverQualitySettings(*values(preset.settings)) == preset.settings
        assert quality_preset(preset.identifier) is preset
        assert apply_quality_preset(preset.identifier) is preset.settings
    assert quality_preset("HIGH").settings == DEFAULT_SOLVER_QUALITY
    with pytest.raises(SolverQualityValidationError, match="unknown"):
        quality_preset("NOT_A_PRESET")


def test_matching_is_derived_with_tight_deterministic_float_tolerance():
    for preset in QUALITY_PRESETS:
        assert matching_quality_preset(preset.settings) is preset
    high = quality_preset("HIGH").settings
    assert matching_quality_preset(replace(
        high, time_step=high.time_step + QUALITY_FLOAT_ABS_TOLERANCE / 2
    )).identifier == "HIGH"
    assert matching_quality_preset(replace(
        high, time_step=high.time_step + QUALITY_FLOAT_ABS_TOLERANCE * 2
    )) is None
    assert matching_quality_preset(replace(high, cg_max_iter=12000)) is None
    assert matching_quality_preset(replace(high, cg_max_iter=10000)).identifier == "HIGH"


def test_every_preset_encodes_the_unchanged_ppf_wire_keys_exactly():
    for preset in QUALITY_PRESETS:
        quality = preset.settings
        payload = build_param_payload(
            SimulationSettings(2, 24, (0.0, 0.0, -9.81), quality),
            "cloth", "cloth-id", "static", "static-id",
            shell=DEFAULT_SHELL_SETTINGS, static=DEFAULT_STATIC_SETTINGS)
        scene = payload["scene"]
        assert scene["dt"] == float32_wire(quality.time_step)
        assert scene["min-newton-steps"] == quality.min_newton_steps
        assert scene["cg-max-iter"] == quality.cg_max_iter
        assert scene["cg-tol"] == float32_wire(quality.cg_tol)


def test_quality_property_labels_ranges_defaults_and_ui_only_foldout(blender_env):
    props = fake_bpy._resolved_props(
        blender_env.object_properties.CLOTHNEXT_PG_solver_quality_settings)
    assert {name: prop.keywords["name"] for name, prop in props.items()} == {
        "time_step": "Motion Step Size",
        "min_newton_steps": "Stability Passes",
        "cg_max_iter": "Maximum Solve Passes",
        "cg_tol": "Solve Accuracy",
        "show_advanced": "Advanced Settings",
    }
    assert [props[name].keywords["default"] for name in
            ("time_step", "min_newton_steps", "cg_max_iter", "cg_tol")] == [
                0.001, 1, 10000, 0.001]
    assert props["show_advanced"].keywords["default"] is False
    assert (props["time_step"].keywords["min"],
            props["time_step"].keywords["max"]) == (0.001, 0.01)
    assert (props["min_newton_steps"].keywords["min"],
            props["min_newton_steps"].keywords["max"]) == (1, 64)
    assert (props["cg_max_iter"].keywords["min"],
            props["cg_max_iter"].keywords["max"]) == (100, 100000)
    assert (props["cg_tol"].keywords["min"],
            props["cg_tol"].keywords["max"]) == (0.00001, 0.1)
    tooltip_terms = {
        "time_step": ("Smaller values", "seconds", "dt"),
        "min_newton_steps": ("Higher values", "take longer",
                             "min-newton-steps"),
        "cg_max_iter": ("Maximum amount of work", "Higher values",
                        "cg-max-iter"),
        "cg_tol": ("Smaller values", "precise", "cg-tol"),
    }
    for name, terms in tooltip_terms.items():
        description = props[name].keywords["description"]
        assert all(term in description for term in terms)
    visible_names = {props[name].keywords["name"] for name in tooltip_terms}
    assert not visible_names & {
        "PCG Max Iterations", "PCG Tolerance", "Minimum Newton Steps",
        "Time Step"}


def test_operator_sets_only_quality_values_and_rejects_invalid_or_active(
        blender_env, monkeypatch, tmp_path):
    env = blender_env
    env.registration.register()
    scene = env.bpy.types.Scene()
    scene.frame_start, scene.frame_end, scene.fps = 3, 42, 30
    cache = tmp_path / "existing.pc2"
    cache.write_bytes(b"existing cache")
    obj = SimpleNamespace(material_marker="unchanged",
                          collision_marker="unchanged",
                          pressure_marker="unchanged")
    context = SimpleNamespace(scene=scene, object=obj, active_object=obj)
    idle = SimpleNamespace(snapshot=lambda: SimpleNamespace(active=False))
    monkeypatch.setattr(env.physics_operators, "shared_controller", idle)
    operator_cls = env.physics_operators.CLOTHNEXT_OT_apply_solver_quality_preset
    for preset in QUALITY_PRESETS:
        operator = operator_cls()
        operator.preset = preset.identifier
        assert operator.execute(context) == {"FINISHED"}
        assert values(env.object_properties.solver_quality_from(scene)) == values(preset.settings)
        assert (scene.frame_start, scene.frame_end, scene.fps) == (3, 42, 30)
        assert (obj.material_marker, obj.collision_marker,
                obj.pressure_marker) == ("unchanged", "unchanged", "unchanged")
        assert cache.read_bytes() == b"existing cache"
    invalid = operator_cls()
    invalid.preset = "INVALID"
    before = values(env.object_properties.solver_quality_from(scene))
    assert invalid.execute(context) == {"CANCELLED"}
    assert values(env.object_properties.solver_quality_from(scene)) == before
    active = SimpleNamespace(snapshot=lambda: SimpleNamespace(active=True))
    monkeypatch.setattr(env.physics_operators, "shared_controller", active)
    blocked = operator_cls()
    blocked.preset = "LOW"
    assert not operator_cls.poll(context)
    assert blocked.execute(context) == {"CANCELLED"}
    assert values(env.object_properties.solver_quality_from(scene)) == before
    env.registration.unregister()


def test_foldout_is_not_fingerprinted_and_numeric_changes_are():
    base = settings_fingerprint(DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS,
                                True, "DEFAULT",
                                quality=DEFAULT_SOLVER_QUALITY)
    same = settings_fingerprint(DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS,
                                True, "DEFAULT",
                                quality=SolverQualitySettings())
    assert same == base
    changed = settings_fingerprint(
        DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS, True, "DEFAULT",
        quality=quality_preset("MEDIUM").settings)
    assert changed != base
    assert settings_fingerprint(
        DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS, True, "DEFAULT",
        quality=DEFAULT_SOLVER_QUALITY) == base


class QualityLayout:
    def __init__(self, sink=None):
        self.sink = sink or self
        if sink is None:
            self.labels, self.props, self.operators = [], [], []
        self.enabled = True
        self.use_property_split = False
        self.use_property_decorate = False

    def column(self, align=False):
        return QualityLayout(self.sink)

    def row(self, align=False):
        return QualityLayout(self.sink)

    def label(self, text="", **_kwargs):
        self.sink.labels.append(text)

    def prop(self, _data, name, **kwargs):
        self.sink.props.append((name, self.enabled, kwargs))

    def operator(self, identifier, **kwargs):
        result = SimpleNamespace()
        self.sink.operators.append((identifier, self.enabled, kwargs, result))
        return result


def test_quality_ui_buttons_custom_foldout_and_active_disable(blender_env):
    env = blender_env
    env.registration.register()
    scene = env.bpy.types.Scene()
    context = SimpleNamespace(scene=scene)
    layout = QualityLayout()
    env.physics_ui._draw_solver_quality(layout, context, False)
    assert [entry[2]["text"] for entry in layout.operators] == [
        "Low", "Medium", "High", "Extreme"]
    assert [entry[2]["depress"] for entry in layout.operators] == [
        False, False, True, False]
    assert all(entry[1] for entry in layout.operators)
    assert "High" in layout.labels
    assert [entry[0] for entry in layout.props] == ["show_advanced"]

    scene.cloth_next_quality.cg_max_iter = 12000
    custom = QualityLayout()
    env.physics_ui._draw_solver_quality(custom, context, False)
    assert "Custom" in custom.labels
    assert "Manually adjusted solver settings." in custom.labels
    assert not any(entry[2]["depress"] for entry in custom.operators)

    scene.cloth_next_quality.show_advanced = True
    active = QualityLayout()
    env.physics_ui._draw_solver_quality(active, context, True)
    assert not any(entry[1] for entry in active.operators)
    advanced = [entry for entry in active.props if entry[0] != "show_advanced"]
    assert [entry[0] for entry in advanced] == [
        "time_step", "min_newton_steps", "cg_max_iter", "cg_tol"]
    assert not any(entry[1] for entry in advanced)
    env.registration.unregister()
