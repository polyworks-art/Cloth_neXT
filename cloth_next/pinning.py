# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure immutable Phase-3C.1 static pin model (never imports ``bpy``)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

STATIC_PIN_WEIGHT_THRESHOLD = 1e-6
PIN_SCHEMA_VERSION = 1


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
        record = {
            "version": PIN_SCHEMA_VERSION,
            "enabled": bool(self.enabled),
            "group": self.group_name,
            "object": self.source_object_id,
            "vertex_count": self.source_vertex_count,
            "indices": indices,
            "threshold": self.threshold,
            "topology": self.source_topology_signature,
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


def static_pin_config(snapshot: StaticPinSnapshot) -> StaticPinConfig | None:
    if not snapshot.enabled:
        return None
    group_id = "cn-pin-v1-" + hashlib.sha256(
        f"{snapshot.source_object_id}\0{snapshot.group_name}".encode("utf-8")
    ).hexdigest()[:24]
    return StaticPinConfig(snapshot.vertex_indices, pin_group_id=group_id)
