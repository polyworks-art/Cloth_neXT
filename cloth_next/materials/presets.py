# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only bundled material presets (pure Python, no ``bpy``).

The packaged ``ppf_fabric_presets.toml`` is parsed and validated exactly
once (cached); Panel draw code must never trigger a file read. A malformed
bundle raises :class:`PresetError` with a visible message and applies
nothing — the parse is all-or-nothing, so a broken file can never leave a
half-applied preset behind.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
import math
from pathlib import Path

from .models import ShellMaterialSettings

PRESET_CUSTOM = "CUSTOM"
CUSTOM_LABEL = "Custom"
CUSTOM_DESCRIPTION = ("Manually edited values; selecting Custom never "
                      "changes the current settings")
DEFAULT_PRESET_ID = "DEFAULT_CLOTH"

CATEGORY_ORDER = (
    "ESSENTIALS", "LIGHTWEIGHT", "NATURAL_WOVENS", "KNITS_STRETCH",
    "PILE_SOFT", "HEAVY_STRUCTURED", "TECHNICAL_COATED",
)
CATEGORY_LABELS = {
    "ESSENTIALS": "Essentials",
    "LIGHTWEIGHT": "Light & Flowing",
    "NATURAL_WOVENS": "Natural Wovens",
    "KNITS_STRETCH": "Knits & Stretch",
    "PILE_SOFT": "Pile & Soft",
    "HEAVY_STRUCTURED": "Heavy & Structured",
    "TECHNICAL_COATED": "Technical & Coated",
}

_PRESET_FILE = Path(__file__).resolve().parent / "ppf_fabric_presets.toml"

_REQUIRED_KEYS = frozenset({
    "id", "label", "category", "description", "upstream_calibrated", "model",
    "surface_weight", "stretch_resistance", "sideways_response",
    "bend_resistance", "surface_grip", "stretch_limit_enabled",
    "maximum_stretch_percent",
})
_OPTIONAL_KEYS = frozenset({
    "shape_damping", "fold_damping", "collision_gap",
    "surface_offset", "source_reference", "measured_area_weight_oz_yd2",
    "measured_bending_stiffness_lbf_in2",
})
_METADATA_KEYS = frozenset({
    "id", "label", "category", "description", "upstream_calibrated",
    "source_reference", "measured_area_weight_oz_yd2",
    "measured_bending_stiffness_lbf_in2",
})
_REQUIRED_PROVENANCE = frozenset({
    "source_project", "source_commit", "source_path", "source_license",
})


class PresetError(ValueError):
    """The bundled preset data is unusable; nothing was applied."""


@dataclass(frozen=True, slots=True)
class MaterialPreset:
    """One immutable, read-only bundled preset."""

    identifier: str
    label: str
    category: str
    description: str
    upstream_calibrated: bool
    source_reference: str | None
    measured_area_weight_oz_yd2: float | None
    measured_bending_stiffness_lbf_in2: float | None
    settings: ShellMaterialSettings


def parse_presets(text: str) -> tuple[tuple[MaterialPreset, ...],
                                      dict[str, str]]:
    """Parse and fully validate preset TOML text (all-or-nothing)."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PresetError(f"bundled preset file is not valid TOML: {exc}") \
            from exc
    provenance = document.get("provenance")
    if not isinstance(provenance, dict) or \
            not _REQUIRED_PROVENANCE <= set(provenance):
        raise PresetError("bundled preset file is missing the provenance "
                          f"keys {sorted(_REQUIRED_PROVENANCE)}")
    entries = document.get("preset")
    if not isinstance(entries, list) or not entries:
        raise PresetError("bundled preset file contains no [[preset]] "
                          "entries")
    presets: list[MaterialPreset] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise PresetError(f"preset entry {index} is not a table")
        keys = set(entry)
        unknown = keys - _REQUIRED_KEYS - _OPTIONAL_KEYS
        if unknown:
            raise PresetError(f"preset entry {index} has unsupported keys "
                              f"{sorted(unknown)}")
        missing = _REQUIRED_KEYS - keys
        if missing:
            raise PresetError(f"preset entry {index} is missing keys "
                              f"{sorted(missing)}")
        identifier = entry["id"]
        if identifier in seen or identifier == PRESET_CUSTOM:
            raise PresetError(f"duplicate or reserved preset id "
                              f"{identifier!r}")
        seen.add(identifier)
        category = str(entry["category"])
        if category not in CATEGORY_LABELS:
            raise PresetError(f"preset {identifier!r} has unknown category "
                              f"{category!r}")
        measured = {}
        for key in ("measured_area_weight_oz_yd2",
                    "measured_bending_stiffness_lbf_in2"):
            value = entry.get(key)
            if value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError) as exc:
                    raise PresetError(f"preset {identifier!r} has invalid "
                                      f"{key}") from exc
                if not math.isfinite(value) or value <= 0.0:
                    raise PresetError(f"preset {identifier!r} requires a "
                                      f"positive finite {key}")
            measured[key] = value
        material_keys = keys - _METADATA_KEYS
        try:
            settings = ShellMaterialSettings(
                **{key: entry[key] for key in material_keys})
        except (TypeError, ValueError) as exc:
            raise PresetError(f"preset {identifier!r} holds invalid "
                              f"material values: {exc}") from exc
        presets.append(MaterialPreset(
            identifier=identifier, label=str(entry["label"]),
            category=category,
            description=str(entry["description"]),
            upstream_calibrated=bool(entry["upstream_calibrated"]),
            source_reference=(str(entry["source_reference"])
                              if entry.get("source_reference") else None),
            measured_area_weight_oz_yd2=
                measured["measured_area_weight_oz_yd2"],
            measured_bending_stiffness_lbf_in2=
                measured["measured_bending_stiffness_lbf_in2"],
            settings=settings))
    return tuple(presets), {key: str(value)
                            for key, value in provenance.items()}


_cache: tuple[tuple[MaterialPreset, ...], dict[str, str]] | None = None
_load_error: str | None = None


def _load() -> tuple[tuple[MaterialPreset, ...], dict[str, str]]:
    global _cache, _load_error
    if _cache is not None:
        return _cache
    if _load_error is not None:
        raise PresetError(_load_error)
    try:
        _cache = parse_presets(_PRESET_FILE.read_text(encoding="utf-8"))
    except (OSError, PresetError) as exc:
        _load_error = str(exc)
        raise PresetError(_load_error) from exc
    return _cache


def builtin_presets() -> tuple[MaterialPreset, ...]:
    """All bundled presets in stable file order (cached single parse)."""
    return _load()[0]


def builtin_provenance() -> dict[str, str]:
    return dict(_load()[1])


def load_error() -> str | None:
    """The cached load failure message, if the bundle is unusable."""
    if _cache is not None:
        return None
    try:
        _load()
    except PresetError:
        pass
    return _load_error


def preset_by_identifier(identifier: str) -> MaterialPreset | None:
    try:
        presets = builtin_presets()
    except PresetError:
        return None
    for preset in presets:
        if preset.identifier == identifier:
            return preset
    return None


def presets_in_category(category: str) -> tuple[MaterialPreset, ...]:
    """Bundled presets in one stable, validated UI category."""
    if category not in CATEGORY_LABELS:
        return ()
    try:
        return tuple(preset for preset in builtin_presets()
                     if preset.category == category)
    except PresetError:
        return ()
