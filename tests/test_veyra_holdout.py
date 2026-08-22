"""Frozen VEYRA holdouts. Do not use these cases while tuning heuristics."""

from cloth_next.veyra.regions import build_regions, solve_region_candidates
from cloth_next.veyra.weld import plan_safe_welds
from tests.veyra_generalization_cases import boundary_seam, region_input


def test_holdout_folded_cuff_coincident_edge_is_not_welded():
    values = list(boundary_seam())
    # Rotate the second patch's interior ninety degrees around the seam.  It is
    # a plausible cuff/pleat, not provably an import seam.
    for offset in (6, 7):
        row = values[offset]
        x, y, _z = row.coordinate
        values[offset] = type(row)(row.index, (0.0, y, x), row.island,
                                   row.boundary, row.attribute_signature,
                                   row.adjacent_vertices, row.adjacent_faces)
    assert plan_safe_welds("cuff", values, local_scale=1.0).clusters == ()


def test_holdout_three_layer_contact_chain_is_ambiguous():
    value = region_input()
    third = region_input(index_offset=50)
    value["triangles"].extend(third["triangles"][:2])
    value["pairs"] = ((0, 2), (1, 3), (2, 50), (3, 51))
    value["authoritative_total"] = 4
    assert build_regions(value).regions[0].ambiguous_sides
    assert solve_region_candidates(value).candidates == ()


def test_holdout_decorative_patch_same_side_is_protected():
    values = boundary_seam(stacked=True, offset=(13.0, -5.0, 2.0))
    plan = plan_safe_welds("decorative-patch", values, local_scale=1.0)
    assert plan.clusters == ()
