# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only bundled material presets (pure Python, no ``bpy``).

Scientific presets and real-world product samples live in separate TOML files
but are parsed, cross-validated and cached as one immutable library. A malformed
bundle poisons the whole load, so the UI can never apply a half-valid preset.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
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
    "PRODUCT_OUTDOOR", "PRODUCT_PERFORMANCE", "PRODUCT_PROTECTIVE",
    "PRODUCT_SHELLS", "PRODUCT_INTERIORS",
)
CATEGORY_LABELS = {
    "ESSENTIALS": "Essentials",
    "LIGHTWEIGHT": "Light & Flowing",
    "NATURAL_WOVENS": "Natural Wovens",
    "KNITS_STRETCH": "Knits & Stretch",
    "PILE_SOFT": "Pile & Soft",
    "HEAVY_STRUCTURED": "Heavy & Structured",
    "TECHNICAL_COATED": "Technical & Coated",
    "PRODUCT_OUTDOOR": "Products · Outdoor Laminates",
    "PRODUCT_PERFORMANCE": "Products · Performance & Stretch",
    "PRODUCT_PROTECTIVE": "Products · Protective",
    "PRODUCT_SHELLS": "Products · Shells & Softshells",
    "PRODUCT_INTERIORS": "Products · Insulation & Interiors",
    # Accepted only for direct parsing of an individual product data pack.
    "PRODUCT_SAMPLES": "Branded Product Samples",
}
_PRODUCT_CATEGORIES = frozenset({
    "PRODUCT_SAMPLES", "PRODUCT_OUTDOOR", "PRODUCT_PERFORMANCE",
    "PRODUCT_PROTECTIVE", "PRODUCT_SHELLS", "PRODUCT_INTERIORS",
})

_MATERIAL_DIR = Path(__file__).resolve().parent
_PRESET_FILE = _MATERIAL_DIR / "ppf_fabric_presets.toml"
_PRODUCT_PRESET_FILES = (
    (_MATERIAL_DIR / "product_fabric_presets.toml", "PRODUCT_OUTDOOR"),
    (_MATERIAL_DIR / "product_performance_presets.toml", "PRODUCT_PERFORMANCE"),
    (_MATERIAL_DIR / "product_protective_presets.toml", "PRODUCT_PROTECTIVE"),
    (_MATERIAL_DIR / "product_shell_presets.toml", "PRODUCT_SHELLS"),
    (_MATERIAL_DIR / "product_interior_presets.toml", "PRODUCT_INTERIORS"),
)

_REQUIRED_KEYS = frozenset({
    "id", "label", "category", "description", "upstream_calibrated", "model",
    "surface_weight", "stretch_resistance", "sideways_response",
    "bend_resistance", "surface_grip", "stretch_limit_enabled",
    "maximum_stretch_percent",
})
_OPTIONAL_KEYS = frozenset({
    "shape_damping", "fold_damping", "collision_gap", "surface_offset",
    "source_reference", "measured_area_weight_oz_yd2",
    "measured_area_weight_g_m2", "measured_bending_stiffness_lbf_in2",
    "product_sample", "manufacturer", "product_style", "data_basis",
    "data_quality",
})
_METADATA_KEYS = frozenset({
    "id", "label", "category", "description", "upstream_calibrated",
    "source_reference", "measured_area_weight_oz_yd2",
    "measured_area_weight_g_m2", "measured_bending_stiffness_lbf_in2",
    "product_sample", "manufacturer", "product_style", "data_basis",
    "data_quality",
})
_REQUIRED_PROVENANCE = frozenset({
    "source_project", "source_commit", "source_path", "source_license",
})
_DATA_QUALITY = frozenset({
    "SCIENTIFIC_MEASUREMENT", "OFFICIAL_PRODUCT_DATA",
    "PUBLISHED_PRODUCT_SAMPLE", "CALIBRATED_REFERENCE",
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
    measured_area_weight_g_m2: float | None
    measured_bending_stiffness_lbf_in2: float | None
    product_sample: bool
    manufacturer: str | None
    product_style: str | None
    data_basis: str | None
    data_quality: str
    settings: ShellMaterialSettings


def _optional_text(entry: dict, key: str) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise PresetError(
            f"preset {entry.get('id')!r} requires a non-empty {key}")
    return text


def parse_presets(
        text: str, *, category_override: str | None = None
        ) -> tuple[tuple[MaterialPreset, ...], dict[str, str]]:
    """Parse and fully validate one preset TOML bundle.

    Product data packs deliberately store the neutral ``PRODUCT_SAMPLES``
    category. The bundled loader assigns each pack to one stable UI section;
    direct tests and third-party tooling can still parse a pack by itself.
    """
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
        raise PresetError("bundled preset file contains no [[preset]] entries")
    if category_override is not None and category_override not in _PRODUCT_CATEGORIES:
        raise PresetError(f"unsupported product category override "
                          f"{category_override!r}")

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

        identifier = str(entry["id"])
        if identifier in seen or identifier == PRESET_CUSTOM:
            raise PresetError(
                f"duplicate or reserved preset id {identifier!r}")
        seen.add(identifier)
        file_category = str(entry["category"])
        if category_override is not None and file_category != "PRODUCT_SAMPLES":
            raise PresetError(
                f"preset {identifier!r} in a product data pack must use "
                "PRODUCT_SAMPLES")
        category = category_override or file_category
        if category not in CATEGORY_LABELS:
            raise PresetError(f"preset {identifier!r} has unknown category "
                              f"{category!r}")

        measured: dict[str, float | None] = {}
        for key in ("measured_area_weight_oz_yd2",
                    "measured_area_weight_g_m2",
                    "measured_bending_stiffness_lbf_in2"):
            value = entry.get(key)
            if value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError) as exc:
                    raise PresetError(
                        f"preset {identifier!r} has invalid {key}") from exc
                if not math.isfinite(value) or value <= 0.0:
                    raise PresetError(f"preset {identifier!r} requires a "
                                      f"positive finite {key}")
            measured[key] = value

        product_sample = bool(entry.get("product_sample", False))
        manufacturer = _optional_text(entry, "manufacturer")
        product_style = _optional_text(entry, "product_style")
        data_basis = _optional_text(entry, "data_basis")
        quality_default = (
            "OFFICIAL_PRODUCT_DATA" if product_sample
            else "SCIENTIFIC_MEASUREMENT"
            if entry.get("source_reference") else "CALIBRATED_REFERENCE")
        data_quality = str(entry.get("data_quality", quality_default))
        if data_quality not in _DATA_QUALITY:
            raise PresetError(f"preset {identifier!r} has unsupported "
                              f"data_quality {data_quality!r}")
        if product_sample:
            if category not in _PRODUCT_CATEGORIES:
                raise PresetError(
                    f"product preset {identifier!r} requires a product category")
            if not all((manufacturer, product_style, data_basis,
                        entry.get("source_reference"))):
                raise PresetError(
                    f"product preset {identifier!r} requires manufacturer, "
                    "product_style, data_basis, and source_reference")
        elif category in _PRODUCT_CATEGORIES:
            raise PresetError(
                f"preset {identifier!r} in a Product section must set "
                "product_sample = true")

        material_keys = keys - _METADATA_KEYS
        try:
            settings = ShellMaterialSettings(
                **{key: entry[key] for key in material_keys})
        except (TypeError, ValueError) as exc:
            raise PresetError(f"preset {identifier!r} holds invalid "
                              f"material values: {exc}") from exc
        presets.append(MaterialPreset(
            identifier=identifier,
            label=str(entry["label"]),
            category=category,
            description=str(entry["description"]),
            upstream_calibrated=bool(entry["upstream_calibrated"]),
            source_reference=(str(entry["source_reference"])
                              if entry.get("source_reference") else None),
            measured_area_weight_oz_yd2=
                measured["measured_area_weight_oz_yd2"],
            measured_area_weight_g_m2=
                measured["measured_area_weight_g_m2"],
            measured_bending_stiffness_lbf_in2=
                measured["measured_bending_stiffness_lbf_in2"],
            product_sample=product_sample,
            manufacturer=manufacturer,
            product_style=product_style,
            data_basis=data_basis,
            data_quality=data_quality,
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
        scientific, provenance = parse_presets(
            _PRESET_FILE.read_text(encoding="utf-8"))
        products: list[MaterialPreset] = []
        combined_provenance = dict(provenance)
        for index, (path, category) in enumerate(
                _PRODUCT_PRESET_FILES, start=1):
            parsed, product_provenance = parse_presets(
                path.read_text(encoding="utf-8"),
                category_override=category)
            products.extend(parsed)
            combined_provenance.update({
                f"product_{index}_{key}": value
                for key, value in product_provenance.items()})
        identifiers = [
            preset.identifier for preset in (*scientific, *products)]
        if len(set(identifiers)) != len(identifiers):
            raise PresetError("bundled preset files contain duplicate ids")
        _cache = ((*scientific, *products), combined_provenance)
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
    return next((preset for preset in presets
                 if preset.identifier == identifier), None)


def presets_in_category(category: str) -> tuple[MaterialPreset, ...]:
    if category not in CATEGORY_LABELS:
        return ()
    try:
        return tuple(preset for preset in builtin_presets()
                     if preset.category == category)
    except PresetError:
        return ()


def product_manufacturers() -> tuple[str, ...]:
    """Stable manufacturer names across all Product sections."""
    try:
        presets = builtin_presets()
    except PresetError:
        return ()
    return tuple(dict.fromkeys(
        preset.manufacturer for preset in presets
        if preset.product_sample and preset.manufacturer))


def product_presets_by_manufacturer(
        manufacturer: str) -> tuple[MaterialPreset, ...]:
    try:
        presets = builtin_presets()
    except PresetError:
        return ()
    return tuple(
        preset for preset in presets
        if preset.product_sample and preset.manufacturer == manufacturer)
