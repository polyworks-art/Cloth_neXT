# SPDX-License-Identifier: GPL-3.0-or-later
"""Newton uses Blender's Z-up, right-handed, metre/kilogram/second convention."""

from __future__ import annotations

import math


def blender_to_newton_position(value):
    result = tuple(float(component) for component in value)
    if len(result) != 3 or not all(map(math.isfinite, result)):
        raise ValueError("position must be a finite three-component vector")
    return result


def newton_to_blender_position(value):
    return blender_to_newton_position(value)


def transform_position(matrix, position):
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        raise ValueError("transform must be a 4x4 matrix")
    x, y, z = blender_to_newton_position(position)
    result = tuple(sum(float(matrix[row][column]) * component
                       for column, component in enumerate((x, y, z, 1.0)))
                   for row in range(3))
    return blender_to_newton_position(result)


def determinant3(matrix) -> float:
    a, b, c = (tuple(float(value) for value in matrix[row][:3])
               for row in range(3))
    return (a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0]))


def validate_world_transform(matrix) -> None:
    if determinant3(matrix) <= 0.0:
        raise ValueError("Newton Live Preview does not support negative or singular object scale")
