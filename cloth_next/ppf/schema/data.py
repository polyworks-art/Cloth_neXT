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
``Z2Y @ matrix_world``), and ``face`` (uint32 triangles). ``uv``, ``stitch``,
``pin``, ``mesh_ref``, and every animation field are optional upstream and
deliberately not emitted in this slice.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Sequence

from ..coordinates import Mat4
from . import envelope

GROUP_SHELL = "SHELL"
GROUP_STATIC = "STATIC"


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

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise SceneEncodeError("object name must not be empty")
        if not self.uuid.strip():
            raise SceneEncodeError("object uuid must not be empty")
        if not self.vertices_local:
            raise SceneEncodeError(f"{self.name}: mesh has no vertices")
        if not self.triangles:
            raise SceneEncodeError(f"{self.name}: mesh has no triangles")
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
        if len(self.transform) != 4 or any(len(r) != 4 for r in self.transform):
            raise SceneEncodeError(f"{self.name}: transform must be 4x4")
        if any(not math.isfinite(c) for row in self.transform for c in row):
            raise SceneEncodeError(f"{self.name}: non-finite transform")

    def info_dict(self) -> dict:
        return {
            "name": self.name,
            "uuid": self.uuid,
            "vert": [[_float32(c) for c in vertex]
                     for vertex in self.vertices_local],
            "transform": [list(row) for row in self.transform],
            "face": [list(tri) for tri in self.triangles],
        }


def build_scene_payload(cloth: SceneObject, collider: SceneObject) -> list:
    """One SHELL group (the cloth) followed by one STATIC group (collider)."""
    if cloth.uuid == collider.uuid:
        raise SceneEncodeError("cloth and collider must have distinct UUIDs")
    return [
        {"object": [cloth.info_dict()], "type": GROUP_SHELL},
        {"object": [collider.info_dict()], "type": GROUP_STATIC},
    ]


def encode_scene(cloth: SceneObject, collider: SceneObject) -> tuple[bytes, str]:
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
