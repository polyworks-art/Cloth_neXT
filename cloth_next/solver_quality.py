# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated scene-wide PPF solver quality settings (pure Python)."""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_TIME_STEP = 0.001
DEFAULT_MIN_NEWTON_STEPS = 1
DEFAULT_CG_MAX_ITER = 10000
DEFAULT_CG_TOL = 0.001

MIN_TIME_STEP, MAX_TIME_STEP = 0.001, 0.01
MIN_NEWTON_STEPS, MAX_NEWTON_STEPS = 1, 64
MIN_CG_MAX_ITER, MAX_CG_MAX_ITER = 100, 100000
MIN_CG_TOL, MAX_CG_TOL = 0.00001, 0.1


class SolverQualityValidationError(ValueError):
    pass


def _finite_range(label: str, value: float, minimum: float,
                  maximum: float) -> None:
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise SolverQualityValidationError(
            f"{label} = {value!r} is invalid (accepted: {minimum:g} to "
            f"{maximum:g}). Adjust it in Physics Properties > Cloth NeXt > "
            "Solver > Solver Quality.")


@dataclass(frozen=True, slots=True)
class SolverQualitySettings:
    time_step: float = DEFAULT_TIME_STEP
    min_newton_steps: int = DEFAULT_MIN_NEWTON_STEPS
    cg_max_iter: int = DEFAULT_CG_MAX_ITER
    cg_tol: float = DEFAULT_CG_TOL

    def __post_init__(self) -> None:
        _finite_range("Time Step", self.time_step, MIN_TIME_STEP, MAX_TIME_STEP)
        if isinstance(self.min_newton_steps, bool) or not (
                MIN_NEWTON_STEPS <= self.min_newton_steps <= MAX_NEWTON_STEPS):
            raise SolverQualityValidationError(
                f"Minimum Newton Steps = {self.min_newton_steps!r} is invalid "
                f"(accepted: {MIN_NEWTON_STEPS} to {MAX_NEWTON_STEPS}). Adjust "
                "it in Solver > Solver Quality.")
        if isinstance(self.cg_max_iter, bool) or not (
                MIN_CG_MAX_ITER <= self.cg_max_iter <= MAX_CG_MAX_ITER):
            raise SolverQualityValidationError(
                f"PCG Max Iterations = {self.cg_max_iter!r} is invalid "
                f"(accepted: {MIN_CG_MAX_ITER} to {MAX_CG_MAX_ITER}). Adjust "
                "it in Solver > Solver Quality.")
        _finite_range("PCG Tolerance", self.cg_tol, MIN_CG_TOL, MAX_CG_TOL)


DEFAULT_SOLVER_QUALITY = SolverQualitySettings()


@dataclass(frozen=True, slots=True)
class SolverQualityPreset:
    identifier: str
    label: str
    description: str
    settings: SolverQualitySettings
    warning: str = ""


QUALITY_PRESETS = (
    SolverQualityPreset(
        "LOW", "Low",
        "Fast previews for setup, timing and broad motion checks.",
        SolverQualitySettings(0.010, 1, 2500, 0.010)),
    SolverQualityPreset(
        "MEDIUM", "Medium",
        "Balanced working quality for most simulations.",
        SolverQualitySettings(0.005, 1, 5000, 0.005)),
    SolverQualityPreset(
        "HIGH", "High",
        "High-quality simulation for final results and reliable contact.",
        SolverQualitySettings(0.001, 1, 10000, 0.001)),
    SolverQualityPreset(
        "EXTREME", "Extreme",
        "Maximum solve effort for difficult collisions, stiff materials and "
        "convergence problems.",
        SolverQualitySettings(0.001, 4, 25000, 0.0001),
        "Extreme can increase simulation time significantly."),
)

_QUALITY_PRESETS_BY_ID = {preset.identifier: preset
                          for preset in QUALITY_PRESETS}
QUALITY_FLOAT_ABS_TOLERANCE = 1e-9


def quality_preset(identifier: str) -> SolverQualityPreset:
    """Return a stable preset, rejecting unknown identifiers explicitly."""
    try:
        return _QUALITY_PRESETS_BY_ID[str(identifier).upper()]
    except KeyError as exc:
        raise SolverQualityValidationError(
            f"unknown solver quality preset: {identifier!r}") from exc


def apply_quality_preset(identifier: str) -> SolverQualitySettings:
    """Resolve a preset to the four authoritative numeric settings."""
    return quality_preset(identifier).settings


def matching_quality_preset(
        settings: SolverQualitySettings) -> SolverQualityPreset | None:
    """Derive the matching preset from numeric values, or return Custom."""
    for preset in QUALITY_PRESETS:
        candidate = preset.settings
        if (settings.min_newton_steps == candidate.min_newton_steps
                and settings.cg_max_iter == candidate.cg_max_iter
                and math.isclose(settings.time_step, candidate.time_step,
                                 rel_tol=0.0,
                                 abs_tol=QUALITY_FLOAT_ABS_TOLERANCE)
                and math.isclose(settings.cg_tol, candidate.cg_tol,
                                 rel_tol=0.0,
                                 abs_tol=QUALITY_FLOAT_ABS_TOLERANCE)):
            return preset
    return None
