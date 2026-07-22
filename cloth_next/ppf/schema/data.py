# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""PPF 0.11 Scene ("Data") payload for the Phase-3A vertical slice.

Exact reproduction of the subset of ``kinds/scene.rs`` /
``encoder/mesh.py`` (pinned commit ``7193f158``) that one triangulated
cloth SHELL plus one static triangulated collider needs:

``[{"type": "SHELL",  "object": [<cloth info>]},
  {"type": "STATIC", "object": [<collider info>]}]``

Each object info carries exactly ``name``, ``uuid``, ``vert`` (object-local
float32-precision positions), ``transform`` (4x4 row-major float64,
``Z2Y @ matrix_world``), and ``face`` (uint32 triangles). Loose Sewing edges
are emitted through upstream's optional ``stitch`` field. ``uv``, ``mesh_ref``,
and unsupported animation fields remain deliberately absent.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..coordinates import Mat4
from . import envelope

GROUP_SHELL = "SHELL"
GROUP_STATIC = "STATIC"
GROUP_ROD = "ROD"
GROUP_SOLID = "SOLID"
INTERNAL_STATIC_NAME = "__cloth_next_solver_static__"
INTERNAL_STATIC_UUID = "cloth-next-internal-static-v1"


class SceneEncodeError(ValueError):
    pass


def _float32(value: float) -> float:
    """Round-trip through IEEE float32 so the wire carries exactly the
    precision the upstream encoder ships (numpy float32 -> CBOR double)."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


@dataclass(frozen=True, slots=True)
class SceneObject:
    """Immutable, pure-Python description of one exported mesh object."""

    name: str
    uuid: str
    vertices_local: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    transform: Mat4  # solver-space world matrix (Z2Y @ matrix_world)
    pin_indices: tuple[int, ...] = ()
    transform_animation: dict | None = None
    static_deform_animation: dict | None = None
    edges: tuple[tuple[int, int], ...] = ()
    stitch_pairs: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SceneEncodeError("object name must not be empty")
        if not self.uuid.strip():
            raise SceneEncodeError("object uuid must not be empty")
        if not self.vertices_local:
            raise SceneEncodeError(f"{self.name}: mesh has no vertices")
        if not self.triangles and not self.edges:
            raise SceneEncodeError(f"{self.name}: object has no elements")
        count = len(self.vertices_local)
        for vertex in self.vertices_local:
            if len(vertex) != 3 or any(not math.isfinite(c) for c in vertex):
                raise SceneEncodeError(f"{self.name}: non-finite vertex")
        for tri in self.triangles:
            if len(tri) != 3:
                raise SceneEncodeError(f"{self.name}: non-triangle face")
            if len(set(tri)) != 3:
                raise SceneEncodeError(f"{self.name}: degenerate triangle {tri}")
            for index in tri:
                if not 0 <= index < count:
                    raise SceneEncodeError(
                        f"{self.name}: triangle index {index} out of range")
        for edge in self.edges:
            if len(edge) != 2 or edge[0] == edge[1] or any(
                    not 0 <= index < count for index in edge):
                raise SceneEncodeError(f"{self.name}: invalid edge {edge}")
        for pair in self.stitch_pairs:
            if len(pair) != 2 or pair[0] == pair[1] or any(
                    not 0 <= index < count for index in pair):
                raise SceneEncodeError(f"{self.name}: invalid stitch {pair}")
        if tuple(sorted(set(self.pin_indices))) != self.pin_indices or any(
                not 0 <= index < count for index in self.pin_indices):
            raise SceneEncodeError(f"{self.name}: invalid pin indices")
        if len(self.transform) != 4 or any(len(r) != 4 for r in self.transform):
            raise SceneEncodeError(f"{self.name}: transform must be 4x4")
        if any(not math.isfinite(c) for row in self.transform for c in row):
            raise SceneEncodeError(f"{self.name}: non-finite transform")
        if (self.transform_animation is not None
                and self.static_deform_animation is not None):
            raise SceneEncodeError(
                f"{self.name}: collider motion sources are mutually exclusive")
        if self.transform_animation is not None:
            animation = self.transform_animation
            required = ("time", "translation", "quaternion", "scale")
            if any(key not in animation for key in required):
                raise SceneEncodeError(
                    f"{self.name}: incomplete transform animation")
            frame_count = len(animation["time"])
            if frame_count < 2 or any(len(animation[key]) != frame_count
                                      for key in required[1:]):
                raise SceneEncodeError(
                    f"{self.name}: inconsistent transform animation")
            segments = animation.get("segments")
            if segments is None or len(segments) != frame_count - 1:
                raise SceneEncodeError(
                    f"{self.name}: transform animation requires one segment "
                    "per frame interval")
        if self.static_deform_animation is not None:
            animation = self.static_deform_animation
            frames = animation.get("vert_frames")
            times = animation.get("time")
            shape = tuple(getattr(frames, "shape", ()))
            if (times is None or len(times) < 2 or len(shape) != 3
                    or shape[0] != len(times) or shape[1] != count
                    or shape[2] != 3):
                raise SceneEncodeError(
                    f"{self.name}: inconsistent static deformation animation")

    def info_dict(self) -> dict:
        info = {
            "name": self.name,
            "uuid": self.uuid,
            "vert": [[_float32(c) for c in vertex]
                     for vertex in self.vertices_local],
            "transform": [list(row) for row in self.transform],
        }
        if self.triangles:
            info["face"] = [list(tri) for tri in self.triangles]
        if self.edges:
            info["edge"] = [list(edge) for edge in self.edges]
        if self.stitch_pairs:
            # Official PPF loose-edge representation. Each source vertex is
            # constrained directly to the target vertex; duplicated target
            # slots carry zero weight and retain the canonical 4-wide shape.
            indices = [[source, target, target, target]
                       for source, target in self.stitch_pairs]
            weights = [[1.0, 1.0, 0.0, 0.0]
                       for _pair in self.stitch_pairs]
            info["stitch"] = [indices, weights]
        if self.pin_indices:
            info["pin"] = list(self.pin_indices)
        if self.transform_animation is not None:
            info["transform_animation"] = self.transform_animation
        if self.static_deform_animation is not None:
            info["static_deform_animation"] = self.static_deform_animation
        return info


def internal_static_sentinel() -> SceneObject:
    """Tiny remote tetrahedron for PPF builds that require a STATIC group.

    PPF 0.11 remains BUSY when building a scene with no STATIC group.  The
    sentinel is an implementation detail, far outside practical scene space,
    and is added only when the artist supplied no Collider.
    """
    return SceneObject(
        INTERNAL_STATIC_NAME, INTERNAL_STATIC_UUID,
        ((0.0,0.0,0.1),(0.1,0.0,-0.1),(-0.05,0.0866,-0.1),
         (-0.05,-0.0866,-0.1)),
        ((0,1,2),(0,2,3),(0,3,1),(1,3,2)),
        ((1.0,0.0,0.0,1_000_000.0),(0.0,1.0,0.0,1_000_000.0),
         (0.0,0.0,1.0,1_000_000.0),(0.0,0.0,0.0,1.0)))


def _collider_sequence(collider) -> tuple[SceneObject, ...]:
    return (() if collider is None else
            (collider,) if isinstance(collider, SceneObject)
            else tuple(collider))


def build_scene_payload(cloth: SceneObject, collider) -> list:
    """One SHELL group and, when present, one STATIC collider group."""
    return build_multi_deformable_scene_payload(((cloth, GROUP_SHELL),),
                                                collider)


def build_deformable_scene_payload(deformable: SceneObject, collider, *,
                                   group_type: str) -> list:
    return build_multi_deformable_scene_payload(
        ((deformable, group_type),), collider)


def build_multi_deformable_scene_payload(deformables, collider) -> list:
    """Build one scene containing every dynamic object and shared colliders.

    Dynamic objects are grouped by PPF element type while preserving their
    input order within each group.  The group order is fixed so payload hashes
    stay deterministic across runs.
    """
    entries = tuple(deformables)
    if not entries:
        raise SceneEncodeError("at least one deformable is required")
    grouped = {GROUP_SHELL: [], GROUP_ROD: [], GROUP_SOLID: []}
    for deformable, group_type in entries:
        if not isinstance(deformable, SceneObject):
            raise SceneEncodeError("deformables must be SceneObject values")
        if group_type not in grouped:
            raise SceneEncodeError(f"unsupported deformable group: {group_type}")
        grouped[group_type].append(deformable)
    colliders = _collider_sequence(collider)
    uuids = [item.uuid for item, _group in entries]
    uuids.extend(item.uuid for item in colliders)
    if len(set(uuids)) != len(uuids):
        raise SceneEncodeError("deformables and colliders need distinct UUIDs")
    payload = [
        {"object": [item.info_dict() for item in grouped[kind]], "type": kind}
        for kind in (GROUP_SHELL, GROUP_ROD, GROUP_SOLID) if grouped[kind]
    ]
    if colliders:
        payload.append({"object": [item.info_dict() for item in colliders],
                        "type": GROUP_STATIC})
    return payload


def encode_deformable_scene(deformable: SceneObject, collider, *,
                            group_type: str) -> tuple[bytes, str]:
    blob = envelope.dumps_envelope(
        envelope.KIND_SCENE,
        build_deformable_scene_payload(deformable, collider,
                                       group_type=group_type))
    return blob, envelope.payload_sha256(blob)


def encode_multi_deformable_scene(deformables, collider) -> tuple[bytes, str]:
    blob = envelope.dumps_envelope(
        envelope.KIND_SCENE,
        build_multi_deformable_scene_payload(deformables, collider))
    return blob, envelope.payload_sha256(blob)


def encode_multi_deformable_scene_file(deformables, collider, path: Path, *,
                                       progress=None) -> tuple[Path, str]:
    digest = envelope.dump_envelope_file(
        envelope.KIND_SCENE,
        build_multi_deformable_scene_payload(deformables, collider), path,
        progress=progress)
    return path, digest


def encode_scene(cloth: SceneObject, collider) -> tuple[bytes, str]:
    blob = envelope.dumps_envelope(envelope.KIND_SCENE,
                                   build_scene_payload(cloth, collider))
    return blob, envelope.payload_sha256(blob)


def zero_area_triangles(vertices: Sequence[Sequence[float]],
                        triangles: Sequence[Sequence[int]],
                        *, epsilon: float = 1e-12) -> list[int]:
    """Indices of triangles with (near-)zero area, for scene validation."""
    bad: list[int] = []
    for index, (a, b, c) in enumerate(triangles):
        ax, ay, az = vertices[a]
        bx, by, bz = vertices[b]
        cx, cy, cz = vertices[c]
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        if (nx * nx + ny * ny + nz * nz) <= epsilon * epsilon:
            bad.append(index)
    return bad
