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
