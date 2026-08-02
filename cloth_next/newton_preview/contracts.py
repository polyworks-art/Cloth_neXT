# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable Newton preview data; this package never imports Blender or Newton."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any

PROTOCOL_VERSION = 3
NEWTON_VERSION = "1.4.0"
WARP_VERSION = "1.15.0"


@dataclass(frozen=True)
class BackendCapabilities:
    cloth_objects: int = 64
    static_triangle_colliders: bool = True
    gravity: bool = True
    hard_static_pins: bool = True
    self_collision: bool = True
    animated_colliders: bool = True
    deforming_colliders: bool = True
    follow_animation_pins: bool = True
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
class PreviewCloth:
    identifier: str
    mesh: PreviewMesh
    pin_indices: tuple[int, ...]
    material: PreviewMaterial

    def validate(self, *, label: str) -> None:
        if not self.identifier:
            raise ValueError(f"{label} requires an identifier")
        self.mesh.validate(label=label)
        if any(index < 0 or index >= len(self.mesh.vertices)
               for index in self.pin_indices):
            raise ValueError(f"{label} pin index is outside the mesh")
        if len(set(self.pin_indices)) != len(self.pin_indices):
            raise ValueError(f"{label} pin indices must be unique")
        self.material.validate()


@dataclass(frozen=True)
class ColliderAnimation:
    collider_index: int
    samples: tuple[tuple[tuple[float, float, float], ...], ...]

    def validate(self, request: "PreviewCreateRequest") -> None:
        if self.collider_index < 0 or self.collider_index >= len(request.colliders):
            raise ValueError("Newton animated Collider index is outside the scene")
        expected_frames = request.frame_end - request.frame_start + 1
        if len(self.samples) != expected_frames:
            raise ValueError("Newton animated Collider sample count does not match the frame range")
        vertex_count = len(request.colliders[self.collider_index].vertices)
        for sample in self.samples:
            if len(sample) != vertex_count or any(
                    len(vertex) != 3
                    or not all(math.isfinite(float(value)) for value in vertex)
                    for vertex in sample):
                raise ValueError("Newton animated Collider topology or positions are invalid")


@dataclass(frozen=True)
class PinAnimation:
    cloth_index: int
    samples: tuple[tuple[tuple[float, float, float], ...], ...]

    def validate(self, request: "PreviewCreateRequest") -> None:
        if self.cloth_index < 0 or self.cloth_index >= len(request.cloths):
            raise ValueError("Newton animated Pin Cloth index is outside the scene")
        pins = request.cloths[self.cloth_index].pin_indices
        if not pins:
            raise ValueError("Newton animated Pin track requires pinned vertices")
        expected_frames = request.frame_end - request.frame_start + 1
        if len(self.samples) != expected_frames:
            raise ValueError("Newton animated Pin sample count does not match the frame range")
        for sample in self.samples:
            if len(sample) != len(pins) or any(
                    len(vertex) != 3
                    or not all(math.isfinite(float(value)) for value in vertex)
                    for vertex in sample):
                raise ValueError("Newton animated Pin positions are invalid")


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
    additional_cloths: tuple[PreviewCloth, ...] = ()
    collider_animations: tuple[ColliderAnimation, ...] = ()
    pin_animations: tuple[PinAnimation, ...] = ()
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
        identifiers = {"primary"}
        for index, cloth in enumerate(self.additional_cloths):
            cloth.validate(label=f"Cloth {index + 2}")
            if cloth.identifier in identifiers:
                raise ValueError("Newton Cloth identifiers must be unique")
            identifiers.add(cloth.identifier)
        self.quality.validate()
        if self.frame_end < self.frame_start:
            raise ValueError("Newton preview end frame precedes its start frame")
        if not math.isfinite(self.fps) or self.fps <= 0.0:
            raise ValueError("Newton preview FPS must be positive")
        if not math.isfinite(self.time_scale) or self.time_scale <= 0.0:
            raise ValueError("Newton preview time scale must be positive")
        if len(self.gravity) != 3 or not all(map(math.isfinite, self.gravity)):
            raise ValueError("Newton gravity must be a finite vector")
        animated_indices = []
        for animation in self.collider_animations:
            animation.validate(self)
            animated_indices.append(animation.collider_index)
        if len(set(animated_indices)) != len(animated_indices):
            raise ValueError("Newton Collider animation tracks must be unique")
        animated_pin_cloths = []
        for animation in self.pin_animations:
            animation.validate(self)
            animated_pin_cloths.append(animation.cloth_index)
        if len(set(animated_pin_cloths)) != len(animated_pin_cloths):
            raise ValueError("Newton animated Pin tracks must be unique per Cloth")

    @property
    def cloths(self) -> tuple[PreviewCloth, ...]:
        primary = PreviewCloth(
            "primary", self.cloth, self.pin_indices, self.material)
        return (primary, *self.additional_cloths)

    @property
    def total_cloth_vertices(self) -> int:
        return sum(len(cloth.mesh.vertices) for cloth in self.cloths)

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
            additional_cloths=tuple(
                PreviewCloth(
                    identifier=str(item["identifier"]),
                    mesh=PreviewMesh(**_mesh_args(item["mesh"])),
                    pin_indices=tuple(map(int, item.get("pin_indices", ()))),
                    material=PreviewMaterial(**item["material"]))
                for item in value.get("additional_cloths", ())),
            collider_animations=tuple(
                ColliderAnimation(
                    collider_index=int(item["collider_index"]),
                    samples=tuple(tuple(tuple(map(float, vertex))
                                        for vertex in sample)
                                  for sample in item.get("samples", ())))
                for item in value.get("collider_animations", ())),
            pin_animations=tuple(
                PinAnimation(
                    cloth_index=int(item["cloth_index"]),
                    samples=tuple(tuple(tuple(map(float, vertex))
                                        for vertex in sample)
                                  for sample in item.get("samples", ())))
                for item in value.get("pin_animations", ())),
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
        if self.vertex_count != request.total_cloth_vertices:
            raise ValueError("Newton preview result vertex count mismatch")
        if self.frame < request.frame_start or self.frame > request.frame_end:
            raise ValueError("Newton preview result frame is outside the range")
