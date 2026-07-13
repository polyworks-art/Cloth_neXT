# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bundled PPF fabric presets: parsing, provenance, exact pinned values,
validation, ordering, atomicity, and independence (task section 16)."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from cloth_next.materials import ShellMaterialSettings, formatting
from cloth_next.materials import presets as material_presets

REPO_ROOT = Path(__file__).resolve().parents[1]
PRESET_FILE = (REPO_ROOT / "cloth_next" / "materials" /
               "ppf_fabric_presets.toml")

# The exact numeric values of the pinned upstream source
# (blender_addon/presets/materials.toml at 7193f158), in file order, plus
# the non-upstream DEFAULT_CLOTH entry that mirrors the pinned defaults.
EXPECTED_PRESETS = {
    "DEFAULT_CLOTH": (1.0, 1000.0, 0.35, 10.0, 0.5, False, 5.0),
    "SILK": (1.0, 500.0, 0.4, 1.42, 0.25, True, 6.0),
    "FLAG": (1.0, 1000.0, 0.4, 0.83, 0.30, True, 4.0),
    "COTTON": (1.0, 5500.0, 0.35, 4.3, 0.35, True, 5.0),
    "WOOL": (1.0, 2000.0, 0.4, 3.67, 0.40, True, 8.0),
    "DENIM": (1.0, 10000.0, 0.25, 10.0, 0.50, True, 3.0),
    "LEATHER": (1.0, 13000.0, 0.4, 1.8, 0.50, True, 2.0),
}
EXPECTED_ORDER = ["DEFAULT_CLOTH", "SILK", "FLAG", "COTTON", "WOOL",
                  "DENIM", "LEATHER"]


def test_pure_material_models_use_artist_facing_field_contract():
    from cloth_next.materials import StaticMaterialSettings
    assert [field.name for field in fields(ShellMaterialSettings)] == [
        "model", "surface_weight", "stretch_resistance",
        "sideways_response", "bend_resistance", "shape_damping",
        "fold_damping", "surface_grip", "collision_gap",
        "surface_offset", "stretch_limit_enabled",
        "maximum_stretch_percent",
    ]
    assert [field.name for field in fields(StaticMaterialSettings)] == [
        "surface_grip", "collision_gap", "surface_offset",
    ]


def test_every_bundled_preset_parses_and_is_shell():
    presets = material_presets.builtin_presets()
    assert [p.identifier for p in presets] == EXPECTED_ORDER
    for preset in presets:
        # every preset is a Shell material (the only supported group type)
        assert isinstance(preset.settings, ShellMaterialSettings)
        assert preset.settings.model == "FABRIC"
        assert preset.description


def test_official_numeric_values_match_the_pinned_source():
    for identifier, expected in EXPECTED_PRESETS.items():
        (density, young, poisson, bend, grip, limit, percent) = expected
        preset = material_presets.preset_by_identifier(identifier)
        assert preset is not None, identifier
        s = preset.settings
        assert s.surface_weight == density, identifier
        assert s.stretch_resistance == young, identifier
        assert s.sideways_response == poisson, identifier
        assert s.bend_resistance == bend, identifier
        assert s.surface_grip == grip, identifier
        assert s.stretch_limit_enabled is limit, identifier
        assert s.maximum_stretch_percent == percent, identifier
    upstream = [p for p in material_presets.builtin_presets()
                if p.upstream_calibrated]
    assert [p.identifier for p in upstream] == EXPECTED_ORDER[1:]
    default = material_presets.preset_by_identifier("DEFAULT_CLOTH")
    assert default.upstream_calibrated is False


def test_all_preset_values_pass_validation_by_construction():
    # ShellMaterialSettings validates in __post_init__, so simply loading
    # is the proof; re-validate explicitly for clarity.
    from cloth_next.materials.validation import validate_shell_values
    for preset in material_presets.builtin_presets():
        validate_shell_values(preset.settings)


def test_provenance_metadata_exists_and_pins_the_upstream_commit():
    provenance = material_presets.builtin_provenance()
    assert provenance["source_project"] == "st-tech/ppf-contact-solver"
    assert provenance["source_commit"] == \
        "7193f158e3843597070f66cb29af19efd9bdcff7"
    assert provenance["source_path"] == \
        "blender_addon/presets/materials.toml"
    assert provenance["source_license"] == "Apache-2.0"


def test_presets_contain_only_supported_keys():
    import tomllib
    document = tomllib.loads(PRESET_FILE.read_text(encoding="utf-8"))
    allowed = (material_presets._REQUIRED_KEYS
               | material_presets._OPTIONAL_KEYS)
    for entry in document["preset"]:
        assert set(entry) <= allowed, entry["id"]


def test_preset_order_is_stable_and_cached():
    first = material_presets.builtin_presets()
    second = material_presets.builtin_presets()
    assert first is second  # single parse, cached
    assert [p.identifier for p in first] == EXPECTED_ORDER


def test_malformed_preset_bundle_is_atomic():
    good = PRESET_FILE.read_text(encoding="utf-8")
    # not TOML at all
    with pytest.raises(material_presets.PresetError):
        material_presets.parse_presets("not [ valid toml")
    # one bad entry poisons the whole parse — nothing is returned
    bad_value = good.replace("stretch_resistance = 5500.0",
                             "stretch_resistance = -1.0")
    with pytest.raises(material_presets.PresetError):
        material_presets.parse_presets(bad_value)
    unknown_key = good + "\nsolver_path = 'C:/evil.exe'\n"
    with pytest.raises(material_presets.PresetError):
        material_presets.parse_presets(unknown_key)
    missing_provenance = good.replace("source_license", "renamed_key")
    with pytest.raises(material_presets.PresetError):
        material_presets.parse_presets(missing_provenance)
    # the cached good bundle is unaffected by failed parses
    assert [p.identifier for p in material_presets.builtin_presets()] == \
        EXPECTED_ORDER
    assert material_presets.load_error() is None


def test_presets_reference_no_solver_binaries_or_paths():
    text = PRESET_FILE.read_text(encoding="utf-8").lower()
    for forbidden in (".exe", ".dll", ".zip", "c:\\", "c:/", "http://",
                      "https://"):
        assert forbidden not in text, forbidden


def test_presets_require_no_upstream_blender_addon():
    package = REPO_ROOT / "cloth_next" / "materials"
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "import bpy" not in source, path
        assert "import blender_addon" not in source, path
        assert "from blender_addon" not in source, path
        assert "zozo" not in source.lower(), path


def test_unknown_identifier_returns_none():
    assert material_presets.preset_by_identifier("NOT_A_PRESET") is None
    assert material_presets.preset_by_identifier(
        material_presets.PRESET_CUSTOM) is None


def test_fingerprint_changes_for_every_mapped_value():
    from cloth_next.materials import (DEFAULT_SHELL_SETTINGS,
                                      DEFAULT_STATIC_SETTINGS)
    from dataclasses import replace
    base = formatting.settings_fingerprint(
        DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS, True, "DEFAULT_CLOTH")
    assert base == formatting.settings_fingerprint(
        DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS, True, "DEFAULT_CLOTH")
    variants = [
        formatting.settings_fingerprint(
            replace(DEFAULT_SHELL_SETTINGS, **{field: value}),
            DEFAULT_STATIC_SETTINGS, True, "DEFAULT_CLOTH")
        for field, value in (
            ("model", "SHAPE_PRESERVING"), ("surface_weight", 2.0),
            ("stretch_resistance", 5500.0), ("sideways_response", 0.4),
            ("bend_resistance", 4.3), ("shape_damping", 0.01),
            ("fold_damping", 0.01), ("surface_grip", 0.35),
            ("collision_gap", 0.002), ("surface_offset", 0.001),
            ("stretch_limit_enabled", True),
            ("maximum_stretch_percent", 3.0))
    ]
    variants.append(formatting.settings_fingerprint(
        DEFAULT_SHELL_SETTINGS,
        replace(DEFAULT_STATIC_SETTINGS, surface_grip=0.9),
        True, "DEFAULT_CLOTH"))
    variants.append(formatting.settings_fingerprint(
        DEFAULT_SHELL_SETTINGS,
        replace(DEFAULT_STATIC_SETTINGS, collision_gap=0.005),
        True, "DEFAULT_CLOTH"))
    variants.append(formatting.settings_fingerprint(
        DEFAULT_SHELL_SETTINGS,
        replace(DEFAULT_STATIC_SETTINGS, surface_offset=0.002),
        True, "DEFAULT_CLOTH"))
    variants.append(formatting.settings_fingerprint(
        DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS, False,
        "DEFAULT_CLOTH"))
    variants.append(formatting.settings_fingerprint(
        DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS, True, "COTTON"))
    assert len({base, *variants}) == len(variants) + 1
