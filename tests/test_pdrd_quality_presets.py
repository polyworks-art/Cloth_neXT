# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import replace

from cloth_next.solver_quality import (
    DEFAULT_SOLVER_QUALITY,
    PDRD_QUALITY_PRESETS,
    QUALITY_PRESETS,
    STANDARD_QUALITY_PRESETS,
    SolverQualitySettings,
    apply_quality_preset,
    matching_quality_preset,
    quality_presets,
    remap_quality_for_pdrd,
)


def test_pdrd_presets_hold_the_stable_mixed_scene_time_step():
    assert QUALITY_PRESETS is STANDARD_QUALITY_PRESETS
    assert quality_presets(has_pdrd=True) is PDRD_QUALITY_PRESETS
    assert [preset.identifier for preset in PDRD_QUALITY_PRESETS] == [
        "LOW", "MEDIUM", "HIGH", "EXTREME"]
    assert [preset.label for preset in PDRD_QUALITY_PRESETS[1:]] == [
        "XMedium", "XHigh", "XExtreme"]

    assert apply_quality_preset(
        "MEDIUM", has_pdrd=True) == SolverQualitySettings(
            time_step=0.001,
            min_newton_steps=1,
            cg_max_iter=10000,
            cg_tol=0.001,
        )
    assert apply_quality_preset(
        "HIGH", has_pdrd=True) == SolverQualitySettings(
            time_step=0.001,
            min_newton_steps=2,
            cg_max_iter=15000,
            cg_tol=0.00075,
        )
    assert apply_quality_preset(
        "EXTREME", has_pdrd=True) == SolverQualitySettings(
            time_step=0.001,
            min_newton_steps=4,
            cg_max_iter=25000,
            cg_tol=0.0005,
        )
    assert all(
        preset.settings.time_step == 0.001
        for preset in PDRD_QUALITY_PRESETS[1:]
    )


def test_pdrd_presets_raise_effort_without_changing_button_identity():
    standard_high = apply_quality_preset("HIGH")
    pdrd_high = apply_quality_preset("HIGH", has_pdrd=True)

    assert pdrd_high.min_newton_steps > standard_high.min_newton_steps
    assert pdrd_high.cg_max_iter > standard_high.cg_max_iter
    assert pdrd_high.cg_tol < standard_high.cg_tol

    # The existing UI compares preset objects by identity. Family-agnostic
    # matching therefore returns the canonical standard button object.
    assert matching_quality_preset(pdrd_high) is QUALITY_PRESETS[2]
    assert matching_quality_preset(
        pdrd_high, has_pdrd=True) is PDRD_QUALITY_PRESETS[2]
    assert matching_quality_preset(
        pdrd_high, has_pdrd=False) is None


def test_legacy_pdrd_values_keep_their_current_button_identity():
    legacy_values = (
        ("MEDIUM", SolverQualitySettings(0.005, 6, 10000, 0.001)),
        ("HIGH", SolverQualitySettings(0.0025, 12, 20000, 0.0005)),
        ("EXTREME", SolverQualitySettings(0.001, 20, 40000, 0.0001)),
    )
    for identifier, settings in legacy_values:
        index = ("LOW", "MEDIUM", "HIGH", "EXTREME").index(identifier)
        assert matching_quality_preset(
            settings, has_pdrd=True) is PDRD_QUALITY_PRESETS[index]
        assert matching_quality_preset(settings) is QUALITY_PRESETS[index]
        assert matching_quality_preset(settings, has_pdrd=False) is None


def test_pdrd_mode_remaps_known_presets_but_preserves_custom_quality():
    standard_medium = apply_quality_preset("MEDIUM")
    pdrd_medium = apply_quality_preset("MEDIUM", has_pdrd=True)

    assert remap_quality_for_pdrd(
        standard_medium,
        from_has_pdrd=False,
        to_has_pdrd=True,
    ) == pdrd_medium
    assert remap_quality_for_pdrd(
        pdrd_medium,
        from_has_pdrd=True,
        to_has_pdrd=False,
    ) == standard_medium

    assert remap_quality_for_pdrd(
        apply_quality_preset("LOW"),
        from_has_pdrd=False,
        to_has_pdrd=True,
    ) == pdrd_medium

    custom = replace(DEFAULT_SOLVER_QUALITY, cg_max_iter=10001)
    assert remap_quality_for_pdrd(
        custom,
        from_has_pdrd=False,
        to_has_pdrd=True,
    ) == custom
