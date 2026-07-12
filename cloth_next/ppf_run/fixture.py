# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Deterministic Phase-3A test geometry, shared by the standalone harness
and the Blender "Create PPF Test Scene" operator (one implementation, no
test-only fork). Pure Python, no ``bpy``.

Scene layout (meters, Blender Z-up):
- ``CN_Test_Cloth``: 11x11 vertex grid, 1.0 m x 1.0 m, horizontal,
  object origin at world (0, 0, 0.8) — above the collider.
- ``CN_Test_Collider``: UV sphere (a sufficiently subdivided static
  obstacle), radius 0.3 m, object origin at world (0, 0, 0.35).
- Gravity: Blender default (0, 0, -9.81) m/s^2; frames 1..8.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CLOTH_NAME = "CN_Test_Cloth"
COLLIDER_NAME = "CN_Test_Collider"
TEST_COLLECTION_NAME = "Cloth NeXt Test"

CLOTH_SIZE = 1.0
CLOTH_DIVISIONS = 10  # 11 x 11 vertices
CLOTH_HEIGHT = 0.8
COLLIDER_RADIUS = 0.3
COLLIDER_HEIGHT = 0.35
FRAME_START = 1
FRAME_END = 8
DEFAULT_GRAVITY = (0.0, 0.0, -9.81)

Vec3 = tuple[float, float, float]
Tri = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class FixtureMesh:
    name: str
    vertices_local: tuple[Vec3, ...]
    triangles: tuple[Tri, ...]
    world_translation: Vec3

    @property
    def world_matrix(self) -> tuple[tuple[float, float, float, float], ...]:
        tx, ty, tz = self.world_translation
        return ((1.0, 0.0, 0.0, tx), (0.0, 1.0, 0.0, ty),
                (0.0, 0.0, 1.0, tz), (0.0, 0.0, 0.0, 1.0))


def cloth_grid(size: float = CLOTH_SIZE,
               divisions: int = CLOTH_DIVISIONS) -> tuple[tuple[Vec3, ...],
                                                          tuple[Tri, ...]]:
    """Regular horizontal grid centered on the object origin, triangulated
    deterministically without reordering the grid vertices."""
    count = divisions + 1
    half = size / 2.0
    step = size / divisions
    vertices = tuple((-half + x * step, -half + y * step, 0.0)
                     for y in range(count) for x in range(count))
    triangles: list[Tri] = []
    for y in range(divisions):
        for x in range(divisions):
            a = y * count + x
            b = a + 1
            c = a + count
            d = c + 1
            triangles.append((a, b, d))
            triangles.append((a, d, c))
    return vertices, tuple(triangles)


def uv_sphere(radius: float = COLLIDER_RADIUS, rings: int = 12,
              segments: int = 16) -> tuple[tuple[Vec3, ...], tuple[Tri, ...]]:
    """Deterministic UV sphere centered on the object origin."""
    vertices: list[Vec3] = [(0.0, 0.0, radius)]
    for ring in range(1, rings):
        polar = math.pi * ring / rings
        z = radius * math.cos(polar)
        ring_radius = radius * math.sin(polar)
        for segment in range(segments):
            azimuth = 2.0 * math.pi * segment / segments
            vertices.append((ring_radius * math.cos(azimuth),
                             ring_radius * math.sin(azimuth), z))
    vertices.append((0.0, 0.0, -radius))
    bottom = len(vertices) - 1
    triangles: list[Tri] = []
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        triangles.append((0, 1 + segment, 1 + next_segment))
    for ring in range(rings - 2):
        row = 1 + ring * segments
        next_row = row + segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            a, b = row + segment, row + next_segment
            c, d = next_row + segment, next_row + next_segment
            triangles.append((a, c, d))
            triangles.append((a, d, b))
    last_row = 1 + (rings - 2) * segments
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        triangles.append((bottom, last_row + next_segment, last_row + segment))
    return tuple(vertices), tuple(triangles)


def vertical_slice_fixture() -> tuple[FixtureMesh, FixtureMesh]:
    cloth_vertices, cloth_triangles = cloth_grid()
    collider_vertices, collider_triangles = uv_sphere()
    cloth = FixtureMesh(CLOTH_NAME, cloth_vertices, cloth_triangles,
                        (0.0, 0.0, CLOTH_HEIGHT))
    collider = FixtureMesh(COLLIDER_NAME, collider_vertices,
                           collider_triangles, (0.0, 0.0, COLLIDER_HEIGHT))
    return cloth, collider
