# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic union timeline for Blender export capture."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math


@dataclass(frozen=True, slots=True, order=True)
class SamplePoint:
    position: Fraction

    @property
    def frame(self) -> int:
        return self.position.numerator // self.position.denominator

    @property
    def subframe(self) -> float:
        return float(self.position - self.frame)


@dataclass(frozen=True, slots=True)
class ColliderTimeline:
    """Exact dense capture timeline with unchanged logical frame bounds."""

    start: int
    end: int
    samples_per_frame: int
    fps: float
    points: tuple[SamplePoint, ...]

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("end must not precede start")
        if not 1 <= int(self.samples_per_frame) <= 32:
            raise ValueError("samples per frame must be between 1 and 32")
        if not math.isfinite(float(self.fps)) or float(self.fps) <= 0.0:
            raise ValueError("fps must be finite and positive")
        expected = (self.end - self.start) * self.samples_per_frame + 1
        if len(self.points) != expected:
            raise ValueError(
                f"collider timeline has {len(self.points)} samples; "
                f"expected {expected}")
        if (not self.points
                or self.points[0].position != Fraction(self.start)
                or self.points[-1].position != Fraction(self.end)):
            raise ValueError(
                "collider timeline must include the exact first and final frame")

    @property
    def logical_frame_count(self) -> int:
        return self.end - self.start + 1

    @property
    def duration_seconds(self) -> float:
        return float(Fraction(self.end - self.start, 1)) / float(self.fps)

    @property
    def frame_offsets(self) -> tuple[float, ...]:
        return tuple(float(point.position - self.start)
                     for point in self.points)

    @property
    def times(self) -> tuple[float, ...]:
        return tuple(
            float(point.position - self.start) / float(self.fps)
            for point in self.points)


def build_collider_timeline(start: int, end: int, *,
                            samples_per_frame: int, fps: float
                            ) -> ColliderTimeline:
    """Build samples directly from integer global indices, without drift."""
    samples = int(samples_per_frame)
    if not 1 <= samples <= 32:
        raise ValueError("samples per frame must be between 1 and 32")
    points = tuple(
        SamplePoint(Fraction(start * samples + index, samples))
        for index in range((end - start) * samples + 1))
    return ColliderTimeline(
        int(start), int(end), samples, float(fps), points)


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
        if not 1 <= samples <= 32:
            raise ValueError("samples per frame must be between 1 and 32")
        points.update(
            Fraction(start * samples + index, samples)
            for index in range((end - start) * samples + 1))
    return tuple(SamplePoint(point) for point in sorted(points))
