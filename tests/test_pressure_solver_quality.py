# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import replace

import pytest

from cloth_next.materials import (DEFAULT_SHELL_SETTINGS,
                                  DEFAULT_STATIC_SETTINGS,
                                  ShellMaterialSettings)
from cloth_next.materials.formatting import settings_fingerprint
from cloth_next.ppf.schema.params import (SimulationSettings,
                                          build_param_payload, float32_wire,
                                          shell_wire_params,
                                          static_wire_params)
from cloth_next.solver_quality import (
    QUALITY_PRESETS, apply_quality_preset, matching_quality_preset,
    DEFAULT_CG_MAX_ITER, DEFAULT_CG_TOL, DEFAULT_MIN_NEWTON_STEPS,
    DEFAULT_SOLVER_QUALITY, DEFAULT_TIME_STEP, SolverQualitySettings,
    SolverQualityValidationError)


def test_quality_presets_are_complete_and_match_their_values():
    assert [preset.identifier for preset in QUALITY_PRESETS] == [
        "LOW", "MEDIUM", "HIGH", "EXTREME"]
    for preset in QUALITY_PRESETS:
        assert apply_quality_preset(preset.identifier.lower()) == preset.settings
        assert matching_quality_preset(preset.settings) is preset
    assert apply_quality_preset("EXTREME").time_step == 0.0005


def test_custom_quality_has_no_match_and_unknown_preset_is_rejected():
    assert matching_quality_preset(
        replace(DEFAULT_SOLVER_QUALITY, cg_max_iter=10001)) is None
    with pytest.raises(SolverQualityValidationError, match="unknown"):
        apply_quality_preset("missing")


def test_pressure_defaults_and_exact_effective_wire_value():
    assert not DEFAULT_SHELL_SETTINGS.enable_inflate
    assert DEFAULT_SHELL_SETTINGS.inflate_pressure == 0.0
    enabled = replace(DEFAULT_SHELL_SETTINGS, enable_inflate=True,
                      inflate_pressure=10.0)
    disabled = replace(enabled, enable_inflate=False)
    assert shell_wire_params(enabled)["pressure"] == float32_wire(10.0)
    assert shell_wire_params(disabled)["pressure"] == float32_wire(0.0)
    assert "pressure" not in static_wire_params(DEFAULT_STATIC_SETTINGS)


def test_pressure_is_object_local_and_rejects_invalid_values():
    first = ShellMaterialSettings(enable_inflate=True, inflate_pressure=4.0)
    second = ShellMaterialSettings(enable_inflate=True, inflate_pressure=9.0)
    assert first.inflate_pressure != second.inflate_pressure
    for invalid in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ShellMaterialSettings(enable_inflate=True,
                                  inflate_pressure=invalid)


def test_shrink_defaults_validation_and_fingerprint():
    assert DEFAULT_SHELL_SETTINGS.shrink_percent == 0.0
    shrunk = replace(DEFAULT_SHELL_SETTINGS, shrink_percent=5.0)
    assert settings_fingerprint(shrunk, DEFAULT_STATIC_SETTINGS, True,
                                "DEFAULT", quality=DEFAULT_SOLVER_QUALITY) != \
        settings_fingerprint(DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS,
                             True, "DEFAULT", quality=DEFAULT_SOLVER_QUALITY)
    for invalid in (-0.01, 90.01, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ShellMaterialSettings(shrink_percent=invalid)


def test_sewing_defaults_wire_mapping_validation_and_fingerprint():
    assert not DEFAULT_SHELL_SETTINGS.sewing_enabled
    assert DEFAULT_SHELL_SETTINGS.sewing_stiffness == 1.0
    sewn = replace(DEFAULT_SHELL_SETTINGS, sewing_enabled=True,
                    sewing_stiffness=2.5)
    assert shell_wire_params(sewn)["stitch-stiffness"] == float32_wire(2.5)
    base = settings_fingerprint(
        DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS, True, "DEFAULT",
        quality=DEFAULT_SOLVER_QUALITY)
    assert settings_fingerprint(
        sewn, DEFAULT_STATIC_SETTINGS, True, "DEFAULT",
        quality=DEFAULT_SOLVER_QUALITY) != base
    for invalid in (-0.01, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ShellMaterialSettings(sewing_stiffness=invalid)


def test_quality_defaults_and_wire_mapping_are_central_and_exact():
    quality = DEFAULT_SOLVER_QUALITY
    assert (quality.time_step, quality.min_newton_steps,
            quality.cg_max_iter, quality.cg_tol) == (
                DEFAULT_TIME_STEP, DEFAULT_MIN_NEWTON_STEPS,
                DEFAULT_CG_MAX_ITER, DEFAULT_CG_TOL)
    payload = build_param_payload(
        SimulationSettings(2, 24, (0.0, 0.0, -9.81), quality),
        "cloth", "cloth-id", "static", "static-id",
        shell=DEFAULT_SHELL_SETTINGS, static=DEFAULT_STATIC_SETTINGS)
    scene = payload["scene"]
    assert scene["dt"] == float32_wire(DEFAULT_TIME_STEP)
    assert scene["min-newton-steps"] == DEFAULT_MIN_NEWTON_STEPS
    assert scene["cg-max-iter"] == DEFAULT_CG_MAX_ITER
    assert scene["cg-tol"] == float32_wire(DEFAULT_CG_TOL)
    assert "substeps" not in scene


@pytest.mark.parametrize("kwargs", [
    {"time_step": 0.00049}, {"time_step": float("nan")},
    {"min_newton_steps": 0}, {"min_newton_steps": 65},
    {"cg_max_iter": 99}, {"cg_max_iter": 100001},
    {"cg_tol": 0.000001}, {"cg_tol": 0.11},
])
def test_quality_validation_ranges(kwargs):
    with pytest.raises(SolverQualityValidationError):
        SolverQualitySettings(**kwargs)


def test_pressure_and_numeric_quality_values_invalidate_fingerprint():
    base = settings_fingerprint(DEFAULT_SHELL_SETTINGS,
                                DEFAULT_STATIC_SETTINGS, True, "DEFAULT",
                                quality=DEFAULT_SOLVER_QUALITY)
    pressure = replace(DEFAULT_SHELL_SETTINGS, enable_inflate=True,
                       inflate_pressure=10.0)
    assert settings_fingerprint(pressure, DEFAULT_STATIC_SETTINGS, True,
                                "DEFAULT", quality=DEFAULT_SOLVER_QUALITY) != base
    for quality in (
        replace(DEFAULT_SOLVER_QUALITY, time_step=0.002),
        replace(DEFAULT_SOLVER_QUALITY, min_newton_steps=2),
        replace(DEFAULT_SOLVER_QUALITY, cg_max_iter=10001),
        replace(DEFAULT_SOLVER_QUALITY, cg_tol=0.002),
    ):
        assert settings_fingerprint(DEFAULT_SHELL_SETTINGS,
                                    DEFAULT_STATIC_SETTINGS, True, "DEFAULT",
                                    quality=quality) != base
