# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Immutable Phase-3B material settings (pure Python, no ``bpy``).

Artist-facing field names differ from the PPF wire spellings on purpose;
the explicit, tested mapping lives in ``cloth_next.ppf.schema.params`` and
is documented in ``docs/PPF_PARAMETER_MAPPING.md``.

``stretch_resistance`` is the DIRECT density-normalized PPF ``young-mod``
wire value (the convention the bundled fabric presets are calibrated in).
It is never divided by density anywhere in Cloth NeXt, and it is not an
ordinary textbook modulus in pascals.
"""

from __future__ import annotations

from dataclasses import dataclass

from .validation import (MODEL_FABRIC, MODEL_SHAPE_PRESERVING, SHELL_MODELS,
                         MaterialValidationError, validate_shell_values,
                         validate_static_values)

__all__ = ["MODEL_FABRIC", "MODEL_SHAPE_PRESERVING", "SHELL_MODELS",
           "WIRE_MODEL_NAMES", "MaterialValidationError",
           "ShellMaterialSettings", "StaticMaterialSettings",
           "DEFAULT_SHELL_SETTINGS", "DEFAULT_STATIC_SETTINGS"]

# Exact wire spellings accepted by the pinned solver (encoder/params.py
# ``model_map`` at commit 7193f158).
WIRE_MODEL_NAMES: dict[str, str] = {
    MODEL_FABRIC: "baraff-witkin",
    MODEL_SHAPE_PRESERVING: "arap",
}


@dataclass(frozen=True, slots=True)
class ShellMaterialSettings:
    """Complete immutable material state for the one simulated cloth.

    Defaults are the pinned upstream shell defaults (the DEFAULT CLOTH
    preset). All values are validated on construction; no Blender objects,
    paths, sockets, or processes are ever stored here.
    """

    model: str = MODEL_FABRIC
    surface_weight: float = 1.0           # kg/m² -> PPF density
    stretch_resistance: float = 1000.0    # direct PPF young-mod (Pa/rho)
    sideways_response: float = 0.35       # -> PPF poiss-rat
    bend_resistance: float = 10.0         # -> PPF bend
    shape_damping: float = 0.0            # s -> PPF deformation-damping
    fold_damping: float = 0.0             # s -> PPF bending-damping
    surface_grip: float = 0.5             # -> PPF friction
    collision_gap: float = 0.001          # world units -> PPF contact-gap
    surface_offset: float = 0.0           # world units -> PPF contact-offset
    stretch_limit_enabled: bool = False   # -> PPF strain-limit on/off
    maximum_stretch_percent: float = 5.0  # % -> PPF strain-limit fraction
    enable_inflate: bool = False          # object-specific pressure toggle
    inflate_pressure: float = 0.0         # -> PPF pressure
    shrink_percent: float = 0.0           # % contraction -> PPF shrink-x/y
    sewing_enabled: bool = False          # loose edges -> PPF stitches
    sewing_stiffness: float = 1.0         # -> PPF stitch-stiffness

    def __post_init__(self) -> None:
        validate_shell_values(self)


@dataclass(frozen=True, slots=True)
class StaticMaterialSettings:
    """Immutable contact state for the one static collider."""

    surface_grip: float = 0.5     # -> PPF friction
    collision_gap: float = 0.001  # world units -> PPF contact-gap
    surface_offset: float = 0.0   # world units -> PPF contact-offset

    def __post_init__(self) -> None:
        validate_static_values(self)


DEFAULT_SHELL_SETTINGS = ShellMaterialSettings()
DEFAULT_STATIC_SETTINGS = StaticMaterialSettings()
