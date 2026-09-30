# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cloth_next.attachments import shortest_mesh_path
from cloth_next.sewing import (SewingError, interaction_visible,
                               merge_stitch_pairs, path_mapping,
                               solver_stitch_stiffness)


def test_sewing_path_mapping_preserves_or_flips_artist_direction():
    source = ((0, 0, 0), (1, 0, 0), (2, 0, 0))
    target = ((0, 1, 0), (1, 1, 0), (2, 1, 0))
    assert path_mapping(source, (0, 1, 2), target, (0, 1, 2), flipped=False) == (
        (0, 0), (1, 1), (2, 2))
    assert path_mapping(source, (0, 1, 2), target, (0, 1, 2), flipped=True) == (
        (0, 2), (1, 1), (2, 0))


def test_sewing_rejects_degenerate_or_repeated_paths():
    vertices = ((0, 0, 0), (1, 0, 0))
    with pytest.raises(SewingError, match="two distinct endpoints"):
        path_mapping(vertices, (0,), vertices, (0, 1))
    with pytest.raises(SewingError, match="visit a vertex twice"):
        path_mapping(vertices, (0, 1, 0), vertices, (0, 1))


def test_boundary_preference_avoids_short_interior_shortcut():
    vertices = ((0, 0, 0), (1, 0, 0), (2, 0, 0), (1, .1, 0))
    edges = ((0, 1), (1, 2), (0, 3), (3, 2))
    assert shortest_mesh_path(vertices, edges, 0, 2) == (0, 1, 2)
    assert shortest_mesh_path(
        vertices, edges, 0, 2, boundary_edges=((0, 3), (3, 2)),
        interior_penalty=20) == (0, 3, 2)


def test_legacy_and_explicit_pairs_are_deduplicated():
    assert merge_stitch_pairs(((2, 1),), ((1, 2), (3, 4))) == ((1, 2), (3, 4))


def test_overlay_is_temporarily_suppressed_without_mutating_user_setting():
    assert interaction_visible(True, True)
    assert not interaction_visible(True, True, playback=True)
    assert not interaction_visible(True, True, baking=True)
    assert not interaction_visible(False, True)


def test_artist_strength_is_calibrated_to_solver_force_units():
    assert solver_stitch_stiffness(0.0) == 0.0
    assert solver_stitch_stiffness(100.0) == 50_000.0
    for invalid in (-1.0, float("inf"), float("nan")):
        with pytest.raises(SewingError, match="finite.*negative"):
            solver_stitch_stiffness(invalid)
