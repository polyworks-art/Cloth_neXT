# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bundled PPF material presets: parsing, provenance, exact values,
validation, product metadata, ordering, atomicity, and independence."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from cloth_next.materials import ShellMaterialSettings, formatting
from cloth_next.materials import presets as material_presets

REPO_ROOT = Path(__file__).resolve().parents[1]
PRESET_FILE = (REPO_ROOT / "cloth_next" / "materials" /
               "ppf_fabric_presets.toml")
PRODUCT_FILES = tuple(path for path, _category
                      in material_presets._PRODUCT_PRESET_FILES)
SCIENTIFIC_PRESET_COUNT = 37
PRODUCT_PRESET_COUNT = 38
TOTAL_PRESET_COUNT = SCIENTIFIC_PRESET_COUNT + PRODUCT_PRESET_COUNT

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
RESEARCH_IDS = [f"MIT_FABRIC_{index:02d}" for index in range(1, 31)]
PRODUCT_CATEGORY_COUNTS = {
    "PRODUCT_OUTDOOR": 12,
    "PRODUCT_PERFORMANCE": 9,
    "PRODUCT_PROTECTIVE": 7,
    "PRODUCT_SHELLS": 6,
    "PRODUCT_INTERIORS": 4,
}


def test_pure_material_models_use_artist_facing_field_contract():
    from cloth_next.materials import StaticMaterialSettings
    assert [field.name for field in fields(ShellMaterialSettings)] == [
        "model", "surface_weight", "stretch_resistance",
        "sideways_response", "bend_resistance",
        "stretch_plasticity_enabled", "stretch_plasticity_rate",
        "stretch_plasticity_threshold_percent",
        "bend_plasticity_enabled", "bend_plasticity_rate",
        "bend_plasticity_threshold_degrees", "bend_rest_from_geometry",
        "shape_damping",
        "fold_damping", "surface_grip", "collision_gap",
        "surface_offset", "stretch_limit_enabled",
        "maximum_stretch_percent", "enable_inflate", "inflate_pressure",
        "shrink_percent", "sewing_enabled", "sewing_stiffness",
    ]
    assert [field.name for field in fields(StaticMaterialSettings)] == [
        "surface_grip", "collision_gap", "surface_offset",
    ]


def test_every_bundled_preset_parses_and_is_shell():
    presets = material_presets.builtin_presets()
    assert [p.identifier for p in presets[:7]] == EXPECTED_ORDER
    assert len(presets) == TOTAL_PRESET_COUNT
    assert len({preset.identifier for preset in presets}) == len(presets)
    for preset in presets:
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
    for index in range(1, len(PRODUCT_FILES) + 1):
        assert provenance[f"product_{index}_source_project"]
        assert provenance[f"product_{index}_source_path"] == \
            "docs/MATERIAL_LIBRARY_SOURCES.md"


def _all_data_files():
    return (PRESET_FILE, *PRODUCT_FILES)


def test_presets_contain_only_supported_keys():
    import tomllib
    allowed = (material_presets._REQUIRED_KEYS
               | material_presets._OPTIONAL_KEYS)
    for path in _all_data_files():
        document = tomllib.loads(path.read_text(encoding="utf-8"))
        for entry in document["preset"]:
            assert set(entry) <= allowed, (path.name, entry["id"])


def test_preset_order_is_stable_and_cached():
    first = material_presets.builtin_presets()
    second = material_presets.builtin_presets()
    assert first is second
    assert [p.identifier for p in first[:7]] == EXPECTED_ORDER
    assert [p.source_reference for p in first[7:SCIENTIFIC_PRESET_COUNT]] == \
        RESEARCH_IDS
    assert all(p.product_sample for p in first[SCIENTIFIC_PRESET_COUNT:])


def test_malformed_preset_bundle_is_atomic():
    good = PRESET_FILE.read_text(encoding="utf-8")
    with pytest.raises(material_presets.PresetError):
        material_presets.parse_presets("not [ valid toml")
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
    assert [p.identifier for p in material_presets.builtin_presets()[:7]] == \
        EXPECTED_ORDER
    assert material_presets.load_error() is None


def test_product_pack_requires_neutral_category_and_complete_metadata():
    text = PRODUCT_FILES[0].read_text(encoding="utf-8")
    parsed, _provenance = material_presets.parse_presets(
        text, category_override="PRODUCT_OUTDOOR")
    assert parsed
    assert all(preset.category == "PRODUCT_OUTDOOR" for preset in parsed)
    assert all(preset.product_sample for preset in parsed)
    invalid = text.replace('category = "PRODUCT_SAMPLES"',
                           'category = "TECHNICAL_COATED"', 1)
    with pytest.raises(material_presets.PresetError):
        material_presets.parse_presets(
            invalid, category_override="PRODUCT_OUTDOOR")


def test_presets_reference_no_solver_binaries_or_external_urls():
    text = "\n".join(path.read_text(encoding="utf-8").lower()
                     for path in _all_data_files())
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


def test_research_library_has_complete_categories_and_measurements():
    presets = material_presets.builtin_presets()
    research = presets[7:SCIENTIFIC_PRESET_COUNT]
    assert len(research) == 30
    assert {preset.source_reference for preset in research} == set(RESEARCH_IDS)
    assert all(preset.measured_area_weight_oz_yd2 > 0 for preset in research)
    assert all(preset.measured_bending_stiffness_lbf_in2 > 0
               for preset in research)
    assert sum(len(material_presets.presets_in_category(category))
               for category in material_presets.CATEGORY_ORDER) == \
        TOTAL_PRESET_COUNT
    assert set(p.category for p in presets) == set(
        material_presets.CATEGORY_ORDER)


def test_product_library_is_large_categorized_and_traceable():
    products = material_presets.builtin_presets()[SCIENTIFIC_PRESET_COUNT:]
    assert len(products) == PRODUCT_PRESET_COUNT
    assert all(preset.product_sample for preset in products)
    assert all(preset.manufacturer for preset in products)
    assert all(preset.product_style for preset in products)
    assert all(preset.data_basis for preset in products)
    assert all(preset.source_reference for preset in products)
    assert {preset.data_quality for preset in products} <= \
        material_presets._DATA_QUALITY
    assert {category: len(material_presets.presets_in_category(category))
            for category in PRODUCT_CATEGORY_COUNTS} == PRODUCT_CATEGORY_COUNTS
    manufacturers = set(material_presets.product_manufacturers())
    assert len(manufacturers) == 18
    assert {"adidas", "DuPont", "Yamamoto Corporation", "Pertex",
            "W. L. Gore & Associates", "CORDURA", "3M"} <= manufacturers


def test_representative_official_product_weights_are_pinned():
    cases = {
        "PRODUCT_KEVLAR_K29_7451S": (465.0, 0.465),
        "PRODUCT_NOMEX_450A": (150.0, 0.15),
        "PRODUCT_NOMEX_ARC650": (220.0, 0.22),
        "PRODUCT_TYVEK_400": (41.5, 0.0415),
        "PRODUCT_3M_THINSULATE_TAI1547": (150.0, 0.15),
        "PRODUCT_SUNBRELLA_SLING_SYSTEM_DUNE": (519.9, 0.5199),
    }
    for identifier, (gsm, density) in cases.items():
        preset = material_presets.preset_by_identifier(identifier)
        assert preset is not None, identifier
        assert preset.data_quality == "OFFICIAL_PRODUCT_DATA"
        assert preset.measured_area_weight_g_m2 == pytest.approx(gsm)
        assert preset.settings.surface_weight == pytest.approx(density)


def test_neoprene_family_is_explicit_not_one_generic_guess():
    yamamoto_39 = material_presets.preset_by_identifier(
        "PRODUCT_YAMAMOTO_39_3MM")
    yamamoto_40 = material_presets.preset_by_identifier(
        "PRODUCT_YAMAMOTO_40_3MM")
    sample = material_presets.preset_by_identifier(
        "PRODUCT_NEOPRENE_3MM_DOUBLE_JERSEY")
    assert yamamoto_39 and yamamoto_40 and sample
    assert yamamoto_40.settings.stretch_resistance < \
        yamamoto_39.settings.stretch_resistance
    assert yamamoto_40.settings.maximum_stretch_percent > \
        yamamoto_39.settings.maximum_stretch_percent
    assert sample.data_quality == "PUBLISHED_PRODUCT_SAMPLE"


def test_representative_mit_measurements_and_conversion_are_pinned():
    silk = material_presets.preset_by_identifier("MIT_SILK_LIGHT")
    denim = material_presets.preset_by_identifier("MIT_DENIM")
    canvas = material_presets.preset_by_identifier("MIT_CANVAS")
    assert silk.measured_area_weight_oz_yd2 == pytest.approx(1.975126)
    assert silk.settings.surface_weight == pytest.approx(0.066968)
    assert denim.measured_bending_stiffness_lbf_in2 == pytest.approx(0.025)
    assert canvas.measured_bending_stiffness_lbf_in2 == pytest.approx(0.1)
    for preset in (silk, denim, canvas):
        expected = (preset.measured_bending_stiffness_lbf_in2 * 14.3491
                    / preset.settings.surface_weight)
        assert preset.settings.bend_resistance == pytest.approx(
            expected, abs=0.001)


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
