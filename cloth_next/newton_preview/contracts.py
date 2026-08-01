# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable Newton preview data; this package never imports Blender or Newton."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any

PROTOCOL_VERSION = 1
NEWTON_VERSION = "1.4.0"
WARP_VERSION = "1.15.0"


@dataclass(frozen=True)
class BackendCapabilities:
    cloth_objects: int = 1
    static_triangle_colliders: bool = True
    gravity: bool = True
    hard_static_pins: bool = True
    self_collision: bool = True
    animated_colliders: bool = False
    follow_animation_pins: bool = False
    pressure: bool = False
    sewing: bool = False
    rods: bool = False
    soft_bodies: bool = False


@dataclass(frozen=True)
class PreviewQuality:
    name: str = "BALANCED"
    substeps: int = 4
    iterations: int = 8
    snapshot_cadence: int = 10
    maximum_snapshots: int = 12
    self_collision: bool = True

    def validate(self) -> None:
        if not 1 <= self.substeps <= 32:
            raise ValueError("Newton preview substeps must be between 1 and 32")
        if not 1 <= self.iterations <= 64:
            raise ValueError("Newton preview iterations must be between 1 and 64")
        if not 1 <= self.snapshot_cadence <= 250:
            raise ValueError("Newton snapshot cadence must be between 1 and 250")
        if not 2 <= self.maximum_snapshots <= 128:
            raise ValueError("Newton maximum snapshots must be between 2 and 128")


@dataclass(frozen=True)
class PreviewMaterial:
    surface_density: float
    stretch_stiffness: float
    shear_stiffness: float
    bend_stiffness: float
    stretch_damping: float
    bend_damping: float
    friction: float
    collision_margin: float
    particle_radius: float

    def validate(self) -> None:
        values = asdict(self)
        if not all(math.isfinite(float(value)) for value in values.values()):
            raise ValueError("Newton material parameters must be finite")
        if self.surface_density <= 0.0:
            raise ValueError("Newton surface density must be positive")
        if min(self.stretch_stiffness, self.shear_stiffness,
               self.bend_stiffness, self.stretch_damping,
               self.bend_damping, self.friction,
               self.collision_margin, self.particle_radius) < 0.0:
            raise ValueError("Newton material parameters cannot be negative")


@dataclass(frozen=True)
class PreviewMesh:
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]

    def validate(self, *, label: str) -> None:
        if len(self.vertices) < 3 or not self.triangles:
            raise ValueError(f"{label} must contain vertices and triangles")
        if any(len(vertex) != 3 or not all(math.isfinite(float(v)) for v in vertex)
               for vertex in self.vertices):
            raise ValueError(f"{label} contains invalid vertex coordinates")
        count = len(self.vertices)
        for triangle in self.triangles:
            if (len(triangle) != 3 or len(set(triangle)) != 3
                    or min(triangle) < 0 or max(triangle) >= count):
                raise ValueError(f"{label} contains an invalid triangle")


@dataclass(frozen=True)
class PreviewCreateRequest:
    session_id: str
    scene_identity: str
    cloth: PreviewMesh
    colliders: tuple[PreviewMesh, ...]
    pin_indices: tuple[int, ...]
    material: PreviewMaterial
    quality: PreviewQuality
    frame_start: int
    frame_end: int
    fps: float
    time_scale: float
    gravity: tuple[float, float, float]
    result_directory: str
    solver: str = "VBD"
    protocol_version: int = PROTOCOL_VERSION
    expected_newton_version: str = NEWTON_VERSION
    expected_warp_version: str = WARP_VERSION

    def validate(self) -> None:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("Newton preview protocol version mismatch")
        if not self.session_id or not self.scene_identity:
            raise ValueError("Newton preview requires session and scene identities")
        if self.solver not in {"VBD", "STYLE3D"}:
            raise ValueError("Unsupported Newton preview solver")
        self.cloth.validate(label="Cloth")
        for index, collider in enumerate(self.colliders):
            collider.validate(label=f"Collider {index + 1}")
        if any(index < 0 or index >= len(self.cloth.vertices)
               for index in self.pin_indices):
            raise ValueError("Newton pin index is outside the cloth mesh")
        if len(set(self.pin_indices)) != len(self.pin_indices):
            raise ValueError("Newton pin indices must be unique")
        self.material.validate()
        self.quality.validate()
        if self.frame_end < self.frame_start:
            raise ValueError("Newton preview end frame precedes its start frame")
        if not math.isfinite(self.fps) or self.fps <= 0.0:
            raise ValueError("Newton preview FPS must be positive")
        if not math.isfinite(self.time_scale) or self.time_scale <= 0.0:
            raise ValueError("Newton preview time scale must be positive")
        if len(self.gravity) != 3 or not all(map(math.isfinite, self.gravity)):
            raise ValueError("Newton gravity must be a finite vector")

    def identity(self) -> str:
        value = asdict(self)
        value.pop("result_directory", None)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def to_wire(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_wire(cls, value: dict[str, Any]) -> "PreviewCreateRequest":
        request = cls(
            session_id=str(value["session_id"]),
            scene_identity=str(value["scene_identity"]),
            cloth=PreviewMesh(**_mesh_args(value["cloth"])),
            colliders=tuple(PreviewMesh(**_mesh_args(item))
                            for item in value.get("colliders", ())),
            pin_indices=tuple(map(int, value.get("pin_indices", ()))),
            material=PreviewMaterial(**value["material"]),
            quality=PreviewQuality(**value["quality"]),
            frame_start=int(value["frame_start"]),
            frame_end=int(value["frame_end"]),
            fps=float(value["fps"]), time_scale=float(value["time_scale"]),
            gravity=tuple(map(float, value["gravity"])),
            result_directory=str(value["result_directory"]),
            solver=str(value.get("solver", "VBD")),
            protocol_version=int(value.get("protocol_version", -1)),
            expected_newton_version=str(value.get("expected_newton_version", "")),
            expected_warp_version=str(value.get("expected_warp_version", "")))
        request.validate()
        return request


def _mesh_args(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "vertices": tuple(tuple(map(float, row)) for row in value["vertices"]),
        "triangles": tuple(tuple(map(int, row)) for row in value["triangles"]),
    }


@dataclass(frozen=True)
class PreviewResult:
    session_id: str
    scene_identity: str
    frame: int
    vertex_count: int
    artifact: str
    sha256: str
    complete: bool = True

    def validate_for(self, request: PreviewCreateRequest) -> None:
        if self.session_id != request.session_id:
            raise ValueError("stale Newton preview session result")
        if self.scene_identity != request.scene_identity:
            raise ValueError("stale Newton preview scene result")
        if not self.complete:
            raise ValueError("partial Newton preview result")
        if self.vertex_count != len(request.cloth.vertices):
            raise ValueError("Newton preview result vertex count mismatch")
        if self.frame < request.frame_start or self.frame > request.frame_end:
            raise ValueError("Newton preview result frame is outside the range")
