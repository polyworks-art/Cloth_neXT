# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure Phase-3B material model: immutable settings, bundled PPF fabric
presets, validation, and artist/wire-name formatting. No ``bpy`` anywhere
in this package (enforced by tests)."""

from .models import (DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS,
                     MODEL_FABRIC, MODEL_SHAPE_PRESERVING, SHELL_MODELS,
                     WIRE_MODEL_NAMES, MaterialValidationError,
                     ShellMaterialSettings, StaticMaterialSettings)

__all__ = ["DEFAULT_SHELL_SETTINGS", "DEFAULT_STATIC_SETTINGS",
           "MODEL_FABRIC", "MODEL_SHAPE_PRESERVING", "SHELL_MODELS",
           "WIRE_MODEL_NAMES", "MaterialValidationError",
           "ShellMaterialSettings", "StaticMaterialSettings"]
