# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure immutable Phase-3C.1 static pin model (never imports ``bpy``)."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum

STATIC_PIN_WEIGHT_THRESHOLD = 1e-6
PIN_SCHEMA_VERSION = 2

class PinMode(str, Enum):
    STATIC = "STATIC"
    FOLLOW_ANIMATION = "FOLLOW_ANIMATION"

@dataclass(frozen=True, slots=True)
class AnimatedPinTargetSample:
    blender_frame: int
    positions: tuple[tuple[float, float, float], ...]

    def __post_init__(self):
        positions=tuple(tuple(float(c) for c in point) for point in self.positions)
        if any(len(point)!=3 or any(not math.isfinite(c) for c in point)
               for point in positions):
            raise StaticPinError(
                f"Animated Pin targets contain invalid coordinates at frame {self.blender_frame}.")
        object.__setattr__(self,"positions",positions)


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
        try: mode=PinMode(self.mode)
        except ValueError as exc: raise StaticPinError("Unknown Pin Mode.") from exc
        samples=tuple(self.samples)
        if mode is PinMode.STATIC and samples:
            raise StaticPinError("Static Pinning must not contain target samples.")
        if self.enabled and mode is PinMode.FOLLOW_ANIMATION:
            expected=self.bake_end-self.bake_start+1
            if expected<1 or len(samples)!=expected:
                raise StaticPinError("Animated Pin sample count must match the Bake range.")
            frames=tuple(sample.blender_frame for sample in samples)
            if frames!=tuple(range(self.bake_start,self.bake_end+1)):
                raise StaticPinError("Animated Pin target frames must be ordered and continuous.")
            if any(len(sample.positions)!=len(indices) for sample in samples):
                raise StaticPinError("Every animated Pin sample must contain one position per pinned vertex.")
        if self.fps<1: raise StaticPinError("Bake FPS must be at least 1.")
        object.__setattr__(self,"mode",mode); object.__setattr__(self,"samples",samples)
        record = {
            "version": PIN_SCHEMA_VERSION,
            "enabled": bool(self.enabled),
            "group": self.group_name,
            "object": self.source_object_id,
            "vertex_count": self.source_vertex_count,
            "indices": indices,
            "threshold": self.threshold,
            "topology": self.source_topology_signature,
            "mode": mode.value, "bake_start":self.bake_start,
            "bake_end":self.bake_end, "fps":self.fps,
            "samples":[{"frame":s.blender_frame,"positions":s.positions}
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
    pull_strength: float = 0.0
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
    times=tuple((sample.blender_frame-snapshot.bake_start)/snapshot.fps
                for sample in snapshot.samples)
    positions=tuple(sample.positions for sample in snapshot.samples)
    return StaticPinConfig(snapshot.vertex_indices, pin_group_id=group_id,
                           times=times,positions=positions)
