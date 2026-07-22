# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated scene-wide PPF solver quality settings (pure Python)."""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_TIME_STEP = 0.001
DEFAULT_MIN_NEWTON_STEPS = 1
DEFAULT_CG_MAX_ITER = 10000
DEFAULT_CG_TOL = 0.001

# PPF accepts and tests 5e-4. Keep the established 1e-3 default, while
# exposing a stability step for dense or fast-moving contact scenes.
MIN_TIME_STEP, MAX_TIME_STEP = 0.0005, 0.01
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


STANDARD_QUALITY_PRESETS = (
    SolverQualityPreset(
        "LOW", "Low", "Fast previews for setup and broad motion checks.",
        SolverQualitySettings(0.010, 1, 2500, 0.010)),
    SolverQualityPreset(
        "MEDIUM", "Medium", "Balanced working quality for most simulations.",
        SolverQualitySettings(0.005, 1, 5000, 0.005)),
    SolverQualityPreset(
        "HIGH", "High",
        "High-quality simulation for final results and reliable contact.",
        SolverQualitySettings(0.001, 1, 10000, 0.001)),
    SolverQualityPreset(
        "EXTREME", "Extreme",
        "Maximum solve effort and smaller motion steps for difficult contact.",
        SolverQualitySettings(0.0005, 4, 25000, 0.0001),
        "Extreme can increase simulation time significantly."),
)

# PDRD switches mixed scenes to the reduced rigid solver, where non-rigid
# vertices currently use the cheaper block-Jacobi preconditioner. These presets
# spend more Newton/PCG work to keep Cloth, Rod and Soft Body motion converged
# until the external solver provides a hybrid PDRD/Schwarz path.
PDRD_QUALITY_PRESETS = (
    SolverQualityPreset(
        "LOW", "Low", "Faster preview quality for scenes containing PDRD.",
        SolverQualitySettings(0.005, 4, 5000, 0.005)),
    SolverQualityPreset(
        "MEDIUM", "Medium",
        "Balanced quality for scenes containing PDRD rigid bodies.",
        SolverQualitySettings(0.0025, 8, 10000, 0.001)),
    SolverQualityPreset(
        "HIGH", "High",
        "Stable final quality for mixed PDRD and deformable scenes.",
        SolverQualitySettings(0.001, 16, 25000, 0.0001)),
    SolverQualityPreset(
        "EXTREME", "Extreme",
        "Maximum solve effort for difficult PDRD contact.",
        SolverQualitySettings(0.0005, 32, 50000, 0.00005),
        "Extreme can increase simulation time significantly."),
)

# Backwards-compatible public name used by the Blender UI and existing tests.
# Labels and button identity remain stable; only the values applied by a button
# depend on whether the scene contains an enabled PDRD object.
QUALITY_PRESETS = STANDARD_QUALITY_PRESETS

_STANDARD_PRESETS_BY_ID = {
    preset.identifier: preset for preset in STANDARD_QUALITY_PRESETS}
_PDRD_PRESETS_BY_ID = {
    preset.identifier: preset for preset in PDRD_QUALITY_PRESETS}
QUALITY_FLOAT_ABS_TOLERANCE = 1e-9


def quality_presets(*, has_pdrd: bool = False) \
        -> tuple[SolverQualityPreset, ...]:
    return PDRD_QUALITY_PRESETS if has_pdrd else STANDARD_QUALITY_PRESETS


def quality_preset(identifier: str, *,
                   has_pdrd: bool = False) -> SolverQualityPreset:
    presets = _PDRD_PRESETS_BY_ID if has_pdrd else _STANDARD_PRESETS_BY_ID
    try:
        return presets[str(identifier).upper()]
    except KeyError as exc:
        raise SolverQualityValidationError(
            f"unknown solver quality preset: {identifier!r}") from exc


def apply_quality_preset(identifier: str, *,
                         has_pdrd: bool = False) -> SolverQualitySettings:
    return quality_preset(identifier, has_pdrd=has_pdrd).settings


def _settings_match(left: SolverQualitySettings,
                    right: SolverQualitySettings) -> bool:
    return (
        left.min_newton_steps == right.min_newton_steps
        and left.cg_max_iter == right.cg_max_iter
        and math.isclose(
            left.time_step, right.time_step, rel_tol=0.0,
            abs_tol=QUALITY_FLOAT_ABS_TOLERANCE)
        and math.isclose(
            left.cg_tol, right.cg_tol, rel_tol=0.0,
            abs_tol=QUALITY_FLOAT_ABS_TOLERANCE)
    )


def matching_quality_preset(
        settings: SolverQualitySettings, *,
        has_pdrd: bool | None = None) -> SolverQualityPreset | None:
    """Return the preset represented by ``settings``.

    ``has_pdrd`` selects one exact preset family. When omitted, both families
    are recognized and the canonical standard preset object is returned. The
    latter keeps existing UI button identity checks working for PDRD values.
    """
    if has_pdrd is not None:
        for preset in quality_presets(has_pdrd=has_pdrd):
            if _settings_match(settings, preset.settings):
                return preset
        return None

    for family in (STANDARD_QUALITY_PRESETS, PDRD_QUALITY_PRESETS):
        for preset in family:
            if _settings_match(settings, preset.settings):
                return _STANDARD_PRESETS_BY_ID[preset.identifier]
    return None


def remap_quality_for_pdrd(
        settings: SolverQualitySettings, *,
        from_has_pdrd: bool,
        to_has_pdrd: bool) -> SolverQualitySettings:
    """Move a recognized preset between standard and PDRD values.

    Custom numeric settings are returned unchanged. This lets Blender remap the
    active preset when the first PDRD object is added or the last one removed,
    without overwriting deliberate manual tuning.
    """
    if from_has_pdrd == to_has_pdrd:
        return settings
    current = matching_quality_preset(
        settings, has_pdrd=from_has_pdrd)
    if current is None:
        return settings
    return apply_quality_preset(
        current.identifier, has_pdrd=to_has_pdrd)
