# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The single Blender <-> PPF coordinate transform, centralized.

Verified against the pinned upstream ``blender_addon/core/transform.py``
(commit ``7193f158``):

- Blender is Z-up, the PPF solver is Y-up.
- Positions and directions transform identically:
  Blender ``(x, y, z)`` -> solver ``(x, z, -y)`` and back
  solver ``(x, y, z)`` -> Blender ``(x, -z, y)``.
- Object world matrices are shipped as ``Z2Y @ matrix_world`` where ``Z2Y``
  is the constant matrix below; mesh vertices stay in object-local space.

Every exported position/vector and every imported result goes through this
module; no other file performs axis swaps.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

Vec3 = tuple[float, float, float]
Mat4 = tuple[tuple[float, float, float, float], ...]

# zup_to_yup() from the pinned upstream transform.py, row-major.
ZUP_TO_YUP: Mat4 = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def blender_position_to_ppf(v: Sequence[float]) -> Vec3:
    return (float(v[0]), float(v[2]), -float(v[1]))


def ppf_position_to_blender(v: Sequence[float]) -> Vec3:
    return (float(v[0]), -float(v[2]), float(v[1]))


def blender_vector_to_ppf(v: Sequence[float]) -> Vec3:
    """Directions (gravity, wind, axes) use the same axis swap as positions."""
    return blender_position_to_ppf(v)


def ppf_vector_to_blender(v: Sequence[float]) -> Vec3:
    return ppf_position_to_blender(v)


def matrix_multiply(a: Mat4, b: Mat4) -> Mat4:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4))
        for i in range(4))


def solver_world_matrix(blender_world: Sequence[Sequence[float]]) -> Mat4:
    """The 4x4 ``transform`` field the Scene payload carries: Z2Y @ world."""
    world: Mat4 = tuple(tuple(float(c) for c in row) for row in blender_world)
    if len(world) != 4 or any(len(row) != 4 for row in world):
        raise ValueError("world matrix must be 4x4")
    return matrix_multiply(ZUP_TO_YUP, world)


def matrix_invert(m: Mat4) -> Mat4:
    """Invert a 4x4 matrix via Gauss-Jordan; raises on singular input."""
    size = 4
    augmented = [list(m[i]) + [1.0 if i == j else 0.0 for j in range(size)]
                 for i in range(size)]
    for col in range(size):
        pivot_row = max(range(col, size), key=lambda r: abs(augmented[r][col]))
        pivot = augmented[pivot_row][col]
        if abs(pivot) < 1e-12:
            raise ValueError("matrix is singular and cannot be inverted")
        augmented[col], augmented[pivot_row] = augmented[pivot_row], augmented[col]
        row = augmented[col]
        inverse_pivot = 1.0 / pivot
        for j in range(2 * size):
            row[j] *= inverse_pivot
        for r in range(size):
            if r == col:
                continue
            factor = augmented[r][col]
            if factor != 0.0:
                other = augmented[r]
                for j in range(2 * size):
                    other[j] -= factor * row[j]
    return tuple(tuple(augmented[i][size:]) for i in range(size))


def transform_point(m: Mat4, point: Sequence[float]) -> Vec3:
    x, y, z = float(point[0]), float(point[1]), float(point[2])
    return (
        m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
        m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
        m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3],
    )


def matrix_is_finite_and_invertible(m: Sequence[Sequence[float]]) -> bool:
    import math
    rows = [list(map(float, row)) for row in m]
    if len(rows) != 4 or any(len(row) != 4 for row in rows):
        return False
    if any(not math.isfinite(c) for row in rows for c in row):
        return False
    try:
        matrix_invert(tuple(tuple(row) for row in rows))
    except ValueError:
        return False
    return True


def solver_world_to_object_local(blender_world: Sequence[Sequence[float]]) -> Mat4:
    """Matrix that maps solver world-space results back to Blender object
    local space: ``(Z2Y @ world)^-1`` — exactly the inverse the pinned
    upstream client applies before writing playback data."""
    return matrix_invert(solver_world_matrix(blender_world))


def transform_points(m: Mat4, points: Iterable[Sequence[float]]) -> list[Vec3]:
    return [transform_point(m, p) for p in points]


def transform_points_numpy(m: Sequence[Sequence[float]], positions) -> np.ndarray:
    """Vectorized affine transform, equivalent to transform_points()."""
    matrix = np.asarray(m, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("transform matrix must be finite and 4x4")
    points = np.asarray(positions)
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("positions must have shape (vertex_count, 3)")
    # Keep the matrix computation in float64 like the prior Python-float path;
    # the PC2 writer performs the single final float32 rounding.
    return points @ matrix[:3, :3].T + matrix[:3, 3]
