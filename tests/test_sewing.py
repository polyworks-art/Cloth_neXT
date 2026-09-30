# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from cloth_next.attachments import shortest_mesh_path
from cloth_next.sewing import (SewingError, interaction_visible,
                               merge_stitch_pairs, path_mapping,
                               sampled_path_mapping, sampled_target_point,
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


@pytest.mark.parametrize("flipped", [False, True])
def test_unequal_subdivisions_do_not_collapse_source_vertices(flipped):
    source = tuple((i / 11, 0, 0) for i in range(12))
    target = tuple((i / 9, 1, 0) for i in range(10))
    samples = sampled_path_mapping(source, range(12), target, range(10), flipped=flipped)
    positions = [sampled_target_point(target, a, b, t) for _, a, b, t in samples]
    expected = [(1 - i / 11 if flipped else i / 11) for i in range(12)]
    assert [point[0] for point in positions] == pytest.approx(expected)
    assert len(set(positions)) == 12
    assert [index for index, *_ in samples] == list(range(12))


def test_interpolated_sewing_uses_rest_arc_length_not_index_spacing():
    source = ((0, 0, 0), (.25, 0, 0), (1, 0, 0))
    target = ((0, 1, 0), (.5, 1, 0), (1, 1, 0))
    samples = sampled_path_mapping(source, (0, 1, 2), target, (0, 1, 2))
    assert samples[1] == (1, 0, 1, .5)
    assert sampled_target_point(target, *samples[1][1:]) == (.25, 1, 0)


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
