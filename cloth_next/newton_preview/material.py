# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit experimental mapping from Cloth NeXt controls to Newton VBD."""

from __future__ import annotations

from .contracts import PreviewMaterial


def map_cloth_material(*, surface_weight: float, stretch_resistance: float,
                       sideways_response: float, bend_resistance: float,
                       shape_damping: float, fold_damping: float,
                       friction: float, collision_gap: float,
                       surface_offset: float) -> PreviewMaterial:
    # Newton's triangle FEM values are not PPF physical parity. Preserve
    # monotonic artist intent and report these resolved values in diagnostics.
    stretch = max(0.0, float(stretch_resistance))
    shear = stretch * max(0.05, 1.0 - float(sideways_response))
    separation = max(0.0, float(collision_gap) + float(surface_offset))
    result = PreviewMaterial(
        surface_density=float(surface_weight),
        stretch_stiffness=stretch,
        shear_stiffness=shear,
        bend_stiffness=max(0.0, float(bend_resistance)),
        stretch_damping=max(0.0, float(shape_damping) * stretch * 0.01),
        bend_damping=max(0.0, float(fold_damping)),
        friction=max(0.0, float(friction)),
        collision_margin=separation,
        particle_radius=max(0.0001, separation * 0.5))
    result.validate()
    return result
