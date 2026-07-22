# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validation for the pure Phase-3B material model (no ``bpy``).

Ranges are honest reproductions of the pinned upstream UI/encoder limits
(``blender_addon/ui/object_group.py`` at commit ``7193f158``); where the
upstream property has no hard maximum, only finiteness and the minimum are
enforced here and the soft maximum stays a UI hint.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MODEL_FABRIC = "FABRIC"
MODEL_SHAPE_PRESERVING = "SHAPE_PRESERVING"
SHELL_MODELS = (MODEL_FABRIC, MODEL_SHAPE_PRESERVING)


class MaterialValidationError(ValueError):
    """One invalid material value: property, value, range, and remedy."""

    def __init__(self, property_name: str, value: object,
                 accepted: str, action: str) -> None:
        self.property_name = property_name
        self.value = value
        self.accepted = accepted
        self.action = action
        super().__init__(f"{property_name} = {value!r} is invalid "
                         f"(accepted: {accepted}). {action}")


@dataclass(frozen=True, slots=True)
class NumericRule:
    """Closed validation rule for one float field."""

    minimum: float
    exclusive_minimum: bool
    maximum: float | None
    unit: str
    action: str

    def accepted_text(self) -> str:
        lower = ">" if self.exclusive_minimum else ">="
        text = f"{lower} {self.minimum:g}"
        if self.maximum is not None:
            text += f" and <= {self.maximum:g}"
        if self.unit:
            text += f" [{self.unit}]"
        return text

    def check(self, property_name: str, value: object) -> None:
        if not isinstance(value, (int, float)) or isinstance(value, bool) \
                or not math.isfinite(float(value)):
            raise MaterialValidationError(
                property_name, value, self.accepted_text(),
                "Enter a finite number. " + self.action)
        number = float(value)
        below = (number <= self.minimum if self.exclusive_minimum
                 else number < self.minimum)
        above = self.maximum is not None and number > self.maximum
        if below or above:
            raise MaterialValidationError(property_name, number,
                                          self.accepted_text(), self.action)


_MATERIAL_PANEL = ("Adjust it in Physics Properties > Cloth NeXt > "
                   "Material.")
_DAMPING_PANEL = ("Adjust it in Physics Properties > Cloth NeXt > "
                  "Damping.")
_COLLISION_PANEL = ("Adjust it in Physics Properties > Cloth NeXt > "
                    "Collisions.")

# Shell (cloth) numeric rules. Hard bounds follow the pinned upstream UI:
# density 0.01..10000 kg/m², young-mod hard max 1e9, poiss-rat max 0.4999,
# friction 0..1, strain limit percent 0..100.
SHELL_RULES: dict[str, NumericRule] = {
    "surface_weight": NumericRule(0.0, True, 10000.0, "kg/m²",
                                   _MATERIAL_PANEL),
    "stretch_resistance": NumericRule(0.0, False, 1e9,
                                      "PPF density-normalized young-mod",
                                      _MATERIAL_PANEL),
    "sideways_response": NumericRule(0.0, False, 0.4999, "",
                                     _MATERIAL_PANEL),
    "bend_resistance": NumericRule(0.0, False, None, "", _MATERIAL_PANEL),
    "shape_damping": NumericRule(0.0, False, None, "s",
                                       _DAMPING_PANEL),
    "fold_damping": NumericRule(0.0, False, None, "s", _DAMPING_PANEL),
    "surface_grip": NumericRule(0.0, False, 1.0, "", _COLLISION_PANEL),
    "collision_gap": NumericRule(0.0, False, None, "world units",
                               _COLLISION_PANEL),
    "surface_offset": NumericRule(0.0, False, None, "world units",
                                  _COLLISION_PANEL),
    "maximum_stretch_percent": NumericRule(0.0, True, 100.0, "%",
                                           _MATERIAL_PANEL),
    "inflate_pressure": NumericRule(0.0, False, None, "solver pressure",
                                     _MATERIAL_PANEL),
    "shrink_percent": NumericRule(0.0, False, 90.0, "%",
                                  _MATERIAL_PANEL),
}

STATIC_RULES: dict[str, NumericRule] = {
    "surface_grip": NumericRule(0.0, False, 1.0, "", _COLLISION_PANEL),
    "collision_gap": NumericRule(0.0, False, None, "world units",
                               _COLLISION_PANEL),
    "surface_offset": NumericRule(0.0, False, None, "world units",
                                  _COLLISION_PANEL),
}


def validate_shell_values(values) -> None:
    """Validate a ShellMaterialSettings-shaped object attribute-wise."""
    model = values.model
    if model not in SHELL_MODELS:
        raise MaterialValidationError(
            "model", model, " or ".join(SHELL_MODELS),
            "Select a solver model in the Advanced PPF panel.")
    if not isinstance(values.stretch_limit_enabled, bool):
        raise MaterialValidationError(
            "stretch_limit_enabled", values.stretch_limit_enabled,
            "True or False", _MATERIAL_PANEL)
    if not isinstance(values.enable_inflate, bool):
        raise MaterialValidationError(
            "enable_inflate", values.enable_inflate, "True or False",
            _MATERIAL_PANEL)
    for name, rule in SHELL_RULES.items():
        rule.check(name, getattr(values, name))


def validate_static_values(values) -> None:
    for name, rule in STATIC_RULES.items():
        rule.check(name, getattr(values, name))
