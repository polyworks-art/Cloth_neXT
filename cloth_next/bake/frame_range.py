# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure Blender-to-PPF frame-range contract."""

from __future__ import annotations

from dataclasses import dataclass

BLENDER_FRAME_MIN = -1_048_574
BLENDER_FRAME_MAX = 1_048_574
MAX_OUTPUT_FRAMES = 10_000


class BakeRangeError(ValueError):
    """An artist-selected bake range cannot be represented safely."""


@dataclass(frozen=True)
class BakeFrameRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if isinstance(self.start, bool) or not isinstance(self.start, int):
            raise BakeRangeError("Bake Start must be a whole Blender frame.")
        if isinstance(self.end, bool) or not isinstance(self.end, int):
            raise BakeRangeError("Bake End must be a whole Blender frame.")
        if self.start < BLENDER_FRAME_MIN or self.end > BLENDER_FRAME_MAX:
            raise BakeRangeError(
                f"Bake frames must be between {BLENDER_FRAME_MIN} and "
                f"{BLENDER_FRAME_MAX}.")
        # The pinned PPF path requires at least one simulated step.
        if self.end <= self.start:
            raise BakeRangeError(
                "Bake End must be greater than Bake Start; zero-step PPF "
                "runs are not supported.")
        if self.output_count > MAX_OUTPUT_FRAMES:
            raise BakeRangeError(
                f"The selected Bake range contains {self.output_count} "
                f"frames. The current safety limit is {MAX_OUTPUT_FRAMES} "
                "frames.")

    @property
    def output_count(self) -> int:
        return self.end - self.start + 1

    @property
    def solver_steps(self) -> int:
        return self.end - self.start

    def blender_frame(self, solver_step: int) -> int:
        if not 0 <= solver_step <= self.solver_steps:
            raise BakeRangeError("Solver step is outside the Bake range.")
        return self.start + solver_step

    def progress(self, solver_step: int) -> tuple[int, int]:
        self.blender_frame(solver_step)
        return solver_step + 1, self.output_count
