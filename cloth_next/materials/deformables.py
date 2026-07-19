# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated PPF material settings for rods and volumetric soft bodies."""

from __future__ import annotations

import math
from dataclasses import dataclass


class DeformableMaterialError(ValueError):
    pass


def _number(name: str, value: float, minimum: float, maximum: float) -> None:
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise DeformableMaterialError(
            f"{name} = {value!r} is invalid (accepted: {minimum:g} to "
            f"{maximum:g}).")


@dataclass(frozen=True, slots=True)
class RodMaterialSettings:
    linear_density: float = 1.0
    stretch_resistance: float = 10000.0
    bend_resistance: float = 10.0
    length_factor: float = 1.0
    shape_damping: float = 0.0
    bend_damping: float = 0.0
    surface_grip: float = 0.5
    collision_gap: float = 0.001
    surface_offset: float = 0.0
    stretch_limit: float = 0.0

    def __post_init__(self) -> None:
        _number("Linear Density", self.linear_density, 0.01, 10000.0)
        _number("Stretch Resistance", self.stretch_resistance, 0.0, 1e9)
        _number("Bend Resistance", self.bend_resistance, 0.0, 1e9)
        _number("Rest Length Scale", self.length_factor, 0.01, 10.0)
        _number("Shape Damping", self.shape_damping, 0.0, 1000.0)
        _number("Bend Damping", self.bend_damping, 0.0, 1000.0)
        _number("Friction", self.surface_grip, 0.0, 1.0)
        _number("Collision Gap", self.collision_gap, 0.0, 1e6)
        _number("Surface Offset", self.surface_offset, 0.0, 1e6)
        _number("Stretch Limit", self.stretch_limit, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class SoftBodyMaterialSettings:
    volume_density: float = 100.0
    stretch_resistance: float = 500.0
    poisson_ratio: float = 0.35
    volume_scale: float = 1.0
    shape_damping: float = 0.0
    surface_grip: float = 0.5
    collision_gap: float = 0.001
    surface_offset: float = 0.0
    tetrahedralizer: str = "ftetwild"

    def __post_init__(self) -> None:
        _number("Volume Density", self.volume_density, 0.01, 10000.0)
        _number("Stretch Resistance", self.stretch_resistance, 0.0, 1e9)
        _number("Poisson Ratio", self.poisson_ratio, 0.0, 0.4999)
        _number("Volume Scale", self.volume_scale, 0.01, 10.0)
        _number("Shape Damping", self.shape_damping, 0.0, 1000.0)
        _number("Friction", self.surface_grip, 0.0, 1.0)
        _number("Collision Gap", self.collision_gap, 0.0, 1e6)
        _number("Surface Offset", self.surface_offset, 0.0, 1e6)
        if self.tetrahedralizer not in {"ftetwild", "tetgen"}:
            raise DeformableMaterialError(
                f"Unknown tetrahedralizer: {self.tetrahedralizer!r}")
