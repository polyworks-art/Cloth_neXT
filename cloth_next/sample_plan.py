# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic union timeline for Blender export capture."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True, order=True)
class SamplePoint:
    position: Fraction

    @property
    def frame(self) -> int:
        return self.position.numerator // self.position.denominator

    @property
    def subframe(self) -> float:
        return float(self.position - self.frame)


def build_sample_plan(start: int, end: int, *,
                      collider_samples=(), include_integer_frames=True):
    if end < start:
        raise ValueError("end must not precede start")
    points = set()
    if include_integer_frames:
        points.update(Fraction(frame, 1)
                      for frame in range(start, end + 1))
    for samples_per_frame in collider_samples:
        samples = int(samples_per_frame)
        if not 2 <= samples <= 32:
            raise ValueError("samples per frame must be between 2 and 32")
        points.update(
            Fraction(start * samples + index, samples)
            for index in range((end - start) * samples + 1))
    return tuple(SamplePoint(point) for point in sorted(points))
