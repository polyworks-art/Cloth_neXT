# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure immutable pin model (never imports ``bpy``)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable

STATIC_PIN_WEIGHT_THRESHOLD = 1e-6
PIN_SCHEMA_VERSION = 3


class PinMode(str, Enum):
    STATIC = "STATIC"
    FOLLOW_ANIMATION = "FOLLOW_ANIMATION"


class PinConstraintType(str, Enum):
    SOFT = "SOFT"
    HARD = "HARD"


PinConstraintResolver = Callable[
    [str, str], tuple[PinConstraintType | str, float] | None
]
_constraint_resolver: PinConstraintResolver | None = None


def set_pin_constraint_resolver(
        resolver: PinConstraintResolver | None) -> None:
    """Install the Blender-side resolver used while snapshots are captured.

    The pure model keeps no Blender dependency. Blender registration may provide
    a resolver that maps ``(source_object_id, group_name)`` to the artist's
    constraint type and pull strength. Tests and non-Blender callers simply use
    the dataclass defaults.
    """
    global _constraint_resolver
    _constraint_resolver = resolver


@dataclass(frozen=True, slots=True)
class AnimatedPinTargetSample:
    blender_frame: float
    positions: tuple[tuple[float, float, float], ...]

    def __post_init__(self):
        frame = float(self.blender_frame)
        if not math.isfinite(frame):
            raise StaticPinError("Animated Pin sample time must be finite.")
        positions = tuple(tuple(float(c) for c in point)
                          for point in self.positions)
        if any(len(point) != 3 or any(not math.isfinite(c) for c in point)
               for point in positions):
            raise StaticPinError(
                f"Animated Pin targets contain invalid coordinates at frame "
                f"{self.blender_frame}.")
        object.__setattr__(self, "blender_frame", frame)
        object.__setattr__(self, "positions", positions)


class StaticPinError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StaticPinSnapshot:
    enabled: bool
    group_name: str
    source_object_id: str
    source_vertex_count: int
    vertex_indices: tuple[int, ...]
    threshold: float = STATIC_PIN_WEIGHT_THRESHOLD
    source_topology_signature: str = ""
    fingerprint: str = ""
    mode: PinMode = PinMode.STATIC
    samples: tuple[AnimatedPinTargetSample, ...] = ()
    bake_start: int = 1
    bake_end: int = 1
    fps: int = 24
    constraint_type: PinConstraintType = PinConstraintType.SOFT
    pull_strength: float = 1.0

    def __post_init__(self) -> None:
        indices = tuple(sorted(set(int(i) for i in self.vertex_indices)))
        if self.source_vertex_count < 0:
            raise StaticPinError("source vertex count must not be negative")
        if self.enabled and not self.group_name:
            raise StaticPinError("Select a Pin Group.")
        if self.enabled and not indices:
            raise StaticPinError(
                "The selected Pin Group contains no pinned vertices.")
        if any(i < 0 or i >= self.source_vertex_count for i in indices):
            raise StaticPinError("The Pin Group contains invalid vertex indices.")
        object.__setattr__(self, "vertex_indices", indices)

        try:
            mode = PinMode(self.mode)
        except ValueError as exc:
            raise StaticPinError("Unknown Pin Mode.") from exc

        constraint_type = self.constraint_type
        pull_strength = float(self.pull_strength)
        if self.enabled and _constraint_resolver is not None:
            resolved = _constraint_resolver(
                self.source_object_id, self.group_name)
            if resolved is not None:
                constraint_type, pull_strength = resolved
        try:
            constraint_type = PinConstraintType(constraint_type)
        except ValueError as exc:
            raise StaticPinError("Unknown Pin Constraint Type.") from exc
        pull_strength = float(pull_strength)
        if not math.isfinite(pull_strength) or pull_strength < 0.0:
            raise StaticPinError("Pin Pull Strength must be finite and non-negative.")
        if self.enabled and constraint_type is PinConstraintType.SOFT:
            if pull_strength <= 0.0:
                raise StaticPinError(
                    "Soft Pin Pull Strength must be greater than zero.")
        else:
            # Hard pins are exact Dirichlet constraints. Their hidden soft
            # strength is irrelevant and must not invalidate a finished cache.
            pull_strength = 0.0

        samples = tuple(self.samples)
        if mode is PinMode.STATIC and samples:
            raise StaticPinError("Static Pinning must not contain target samples.")
        if self.enabled and mode is PinMode.FOLLOW_ANIMATION:
            expected = self.bake_end - self.bake_start + 1
            if expected < 1 or len(samples) < expected:
                raise StaticPinError(
                    "Animated Pin samples must cover the complete Bake range.")
            frames = tuple(sample.blender_frame for sample in samples)
            if (not math.isclose(frames[0], float(self.bake_start),
                                 abs_tol=1e-9)
                    or not math.isclose(frames[-1], float(self.bake_end),
                                        abs_tol=1e-9)
                    or any(right <= left
                           for left, right in zip(frames, frames[1:]))):
                raise StaticPinError(
                    "Animated Pin target samples must be ordered and span "
                    "the complete Bake range.")
            sampled_integer_frames = {
                int(round(frame)) for frame in frames
                if math.isclose(frame, round(frame), abs_tol=1e-9)}
            if sampled_integer_frames != set(
                    range(self.bake_start, self.bake_end + 1)):
                raise StaticPinError(
                    "Animated Pin targets must include every Blender frame.")
            if any(len(sample.positions) != len(indices) for sample in samples):
                raise StaticPinError(
                    "Every animated Pin sample must contain one position per "
                    "pinned vertex.")
        if self.fps < 1:
            raise StaticPinError("Bake FPS must be at least 1.")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "samples", samples)
        object.__setattr__(self, "constraint_type", constraint_type)
        object.__setattr__(self, "pull_strength", pull_strength)
        record = {
            "version": PIN_SCHEMA_VERSION,
            "enabled": bool(self.enabled),
            "group": self.group_name,
            "object": self.source_object_id,
            "vertex_count": self.source_vertex_count,
            "indices": indices,
            "threshold": self.threshold,
            "topology": self.source_topology_signature,
            "mode": mode.value,
            "constraint_type": constraint_type.value,
            "pull_strength": pull_strength,
            "bake_start": self.bake_start,
            "bake_end": self.bake_end,
            "fps": self.fps,
            "samples": [
                {"frame": s.blender_frame, "positions": s.positions}
                for s in samples],
        }
        digest = hashlib.sha256(json.dumps(
            record, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        object.__setattr__(self, "fingerprint", digest)


@dataclass(frozen=True, slots=True)
class StaticPinConfig:
    indices: tuple[int, ...]
    operations: tuple = ()
    unpin_time: None = None
    transition: str = "linear"
    pull_strength: float = 1.0
    pin_stiffness: float = 1.0
    pin_group_id: str = ""
    pull_weights: None = None
    rest_shape_track: bool = False
    times: tuple[float, ...] = ()
    positions: tuple[tuple[tuple[float, float, float], ...], ...] = ()


def static_pin_config(snapshot: StaticPinSnapshot) -> StaticPinConfig | None:
    if not snapshot.enabled:
        return None
    group_id = "cn-pin-v1-" + hashlib.sha256(
        f"{snapshot.source_object_id}\0{snapshot.group_name}".encode("utf-8")
    ).hexdigest()[:24]
    times = tuple(
        (float(sample.blender_frame) - snapshot.bake_start) / snapshot.fps
        for sample in snapshot.samples)
    positions = tuple(sample.positions for sample in snapshot.samples)
    return StaticPinConfig(
        snapshot.vertex_indices,
        pin_group_id=group_id,
        pull_strength=snapshot.pull_strength,
        times=times,
        positions=positions)
