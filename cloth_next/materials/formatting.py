# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Artist-name <-> PPF-wire-name presentation helpers (pure, no ``bpy``).

One table maps every dataclass field to its artist-facing UI label and its
exact PPF wire key; the Advanced PPF panel, the parameter inspector, and
the mapping tests all read the same rows so the three can never disagree.
Also owns the material fingerprint used for cache-staleness detection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .models import (ShellMaterialSettings, StaticMaterialSettings,
                     WIRE_MODEL_NAMES)


@dataclass(frozen=True, slots=True)
class FieldInfo:
    field: str          # pure dataclass field name
    artist_label: str   # standard-view UI name
    ppf_key: str        # exact wire spelling
    unit: str


SHELL_FIELD_INFO: tuple[FieldInfo, ...] = (
    FieldInfo("model", "Solver Model", "model", ""),
    FieldInfo("surface_weight", "Surface Weight", "density", "kg/m²"),
    FieldInfo("stretch_resistance", "Stretch Resistance", "young-mod",
              "density-normalized"),
    FieldInfo("sideways_response", "Sideways Response", "poiss-rat", ""),
    FieldInfo("bend_resistance", "Bend Resistance", "bend", ""),
    FieldInfo("shape_damping", "Shape Damping",
              "deformation-damping", "s"),
    FieldInfo("fold_damping", "Fold Damping", "bending-damping", "s"),
    FieldInfo("surface_grip", "Surface Grip", "friction", ""),
    FieldInfo("collision_gap", "Collision Gap", "contact-gap",
              "world units"),
    FieldInfo("surface_offset", "Surface Offset", "contact-offset",
              "world units"),
)

STATIC_FIELD_INFO: tuple[FieldInfo, ...] = (
    FieldInfo("surface_grip", "Surface Grip", "friction", ""),
    FieldInfo("collision_gap", "Collision Gap", "contact-gap",
              "world units"),
    FieldInfo("surface_offset", "Surface Offset", "contact-offset",
              "world units"),
)


def strain_limit_fraction(shell: ShellMaterialSettings) -> float:
    """The encoded PPF ``strain-limit``: percent/100 when enabled, else 0."""
    if not shell.stretch_limit_enabled:
        return 0.0
    return shell.maximum_stretch_percent / 100.0


def shell_wire_rows(shell: ShellMaterialSettings) \
        -> tuple[tuple[str, str, str], ...]:
    """(artist label, ppf key, display value) rows for the cloth."""
    rows: list[tuple[str, str, str]] = []
    for info in SHELL_FIELD_INFO:
        value = getattr(shell, info.field)
        if info.field == "model":
            display = WIRE_MODEL_NAMES[shell.model]
        else:
            display = f"{float(value):g}"
        rows.append((info.artist_label, info.ppf_key, display))
    rows.append(("Maximum Stretch", "strain-limit",
                 f"{strain_limit_fraction(shell):g}"))
    return tuple(rows)


def static_wire_rows(static: StaticMaterialSettings) \
        -> tuple[tuple[str, str, str], ...]:
    return tuple((info.artist_label, info.ppf_key,
                  f"{float(getattr(static, info.field)):g}")
                 for info in STATIC_FIELD_INFO)


FINGERPRINT_VERSION = 1


def settings_fingerprint(shell: ShellMaterialSettings,
                         static: StaticMaterialSettings,
                         contact_enabled: bool,
                         preset_identifier: str,
                         *, bake_start: int | None = None,
                         bake_end: int | None = None) -> str:
    """Deterministic digest of every solver-visible material setting.

    Any change to a mapped value produces a different digest, which marks
    an existing bake result as stale (it is never deleted automatically).
    """
    record = {
        "version": FINGERPRINT_VERSION,
        "preset": preset_identifier,
        "contact_enabled": bool(contact_enabled),
        "shell": {info.field: getattr(shell, info.field)
                  for info in SHELL_FIELD_INFO},
        "shell_stretch_limit_enabled": shell.stretch_limit_enabled,
        "shell_maximum_stretch_percent": shell.maximum_stretch_percent,
        "static": {info.field: getattr(static, info.field)
                   for info in STATIC_FIELD_INFO},
        "bake_range": ([int(bake_start), int(bake_end)]
                       if bake_start is not None and bake_end is not None
                       else None),
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
