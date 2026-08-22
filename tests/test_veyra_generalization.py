from __future__ import annotations

import math
import time

import pytest

from cloth_next.veyra.regions import build_regions, solve_region_candidates
from cloth_next.veyra.weld import conservative_tolerance, plan_safe_welds
from tests.veyra_generalization_cases import boundary_seam, region_input


def coordinate_clusters(plan, vertices):
    by_index = {row.index: row.coordinate for row in vertices}
    return {frozenset(by_index[index] for index in cluster)
            for cluster in plan.clusters}


def test_clean_cloth_has_no_weld_and_no_region_candidates():
    vertices = tuple(row for row in boundary_seam(separation=.25)
                     if row.island == 0)
    assert plan_safe_welds("clean", vertices, local_scale=1.0).clusters == ()
    value = region_input()
    value["pairs"] = (); value["authoritative_total"] = 0
    batch = solve_region_candidates(value)
    assert batch.region_count == 0 and batch.candidates == ()


def test_exact_duplicate_continuous_boundary_is_weldable():
    values = boundary_seam()
    plan = plan_safe_welds("cloth", values, local_scale=1.0,
                           eligible_indices=(0, 1, 2, 3))
    assert plan.clusters == ((0, 1), (2, 3))
    assert plan.merged_vertices == 2


@pytest.mark.parametrize("stacked", [True])
def test_intentional_stacked_layers_fail_closed(stacked):
    values = boundary_seam(stacked=stacked)
    plan = plan_safe_welds("cloth", values, local_scale=1.0,
                           eligible_indices=(0, 1, 2, 3))
    assert plan.clusters == ()
    assert plan.skip_reasons["UNPROVEN_BOUNDARY_SEAM"] >= 2


@pytest.mark.parametrize("relative", [1e-7, 1e-5, 1e-3])
def test_near_duplicate_layers_are_not_radius_merged(relative):
    values = boundary_seam(separation=relative)
    assert plan_safe_welds("cloth", values, local_scale=1.0).clusters == ()


@pytest.mark.parametrize("scale", [.01, 1.0, 100.0])
def test_weld_scale_invariance(scale):
    values = boundary_seam(scale=scale)
    plan = plan_safe_welds("cloth", values, local_scale=scale)
    assert plan.clusters == ((0, 1), (2, 3))
    separated = boundary_seam(scale=scale,
                              separation=conservative_tolerance(1.0) * 2.0)
    assert plan_safe_welds("cloth", separated,
                           local_scale=scale).clusters == ()


def test_weld_translation_rotation_index_and_name_invariance():
    baseline = boundary_seam()
    first = plan_safe_welds("first-name", baseline, local_scale=1.0)
    permutation = {0: 17, 1: 4, 2: 91, 3: 6, 4: 55, 5: 22, 6: 8, 7: 3}
    changed = boundary_seam(angle=math.pi / 3, offset=(17, -8, 4),
                            permutation=permutation)
    second = plan_safe_welds("renamed", changed, local_scale=1.0)
    assert len(first.clusters) == len(second.clusters) == 2
    assert second.merged_vertices == first.merged_vertices


def test_point_attribute_or_constraint_conflict_protects_whole_seam():
    values = list(boundary_seam())
    values[1] = type(values[1])(
        values[1].index, values[1].coordinate, values[1].island,
        values[1].boundary, ("different-pin-weight",),
        values[1].adjacent_vertices, values[1].adjacent_faces)
    plan = plan_safe_welds("cloth", values, local_scale=1.0)
    assert plan.clusters == ()
    assert plan.skip_reasons == {
        "POINT_ATTRIBUTE_CONFLICT": 1, "UNPROVEN_BOUNDARY_SEAM": 1}


def test_three_interacting_sheets_are_ambiguous_not_forced_into_two():
    value = region_input()
    third = region_input(object_uuid="cloth", index_offset=20)
    value["triangles"].extend(third["triangles"][:2])
    value["pairs"] = ((0, 2), (1, 3), (2, 20), (3, 21))
    value["authoritative_total"] = 4
    analysis = build_regions(value)
    assert len(analysis.regions) == 1
    assert analysis.regions[0].ambiguous_sides
    assert solve_region_candidates(value).candidates == ()


@pytest.mark.parametrize("scale", [.01, 1.0, 100.0])
def test_region_planning_is_scale_relative(scale):
    batch = solve_region_candidates(region_input(scale=scale, index_offset=int(scale)))
    leaves = [item for item in batch.candidates if not item.member_candidate_ids]
    assert leaves
    assert {item.amplitude_fraction for item in leaves} == {.01}
    assert all(item.max_displacement <= item.local_scale * .01 + 1e-15
               for item in leaves)


def test_face_order_object_rename_and_rigid_transform_are_equivalent():
    baseline = solve_region_candidates(region_input())
    changed = solve_region_candidates(region_input(
        object_uuid="renamed", angle=math.pi / 2, offset=(4, -2, 7),
        face_order=(3, 1, 0, 2), index_offset=30))
    first = [item for item in baseline.candidates if not item.member_candidate_ids]
    second = [item for item in changed.candidates if not item.member_candidate_ids]
    assert len(first) == len(second)
    assert [item.amplitude_fraction for item in first] == [
        item.amplitude_fraction for item in second]
    assert [item.direction_kind for item in first] == [
        item.direction_kind for item in second]


def test_multiple_independent_regions_batch_deterministically():
    value = region_input()
    for offset in (20, 40):
        other = region_input(index_offset=offset)
        value["triangles"].extend(other["triangles"])
        value["pairs"] += tuple(other["pairs"])
    value["authoritative_total"] = 6
    first = solve_region_candidates(value)
    value["pairs"] = tuple(reversed(value["pairs"]))
    second = solve_region_candidates(value)
    assert first.region_count == second.region_count == 3
    assert [item.candidate_id for item in first.candidates] == [
        item.candidate_id for item in second.candidates]


def test_planning_scaling_smoke_is_bounded():
    samples = []
    for count in (1, 3, 6):
        value = region_input()
        for offset in range(1, count):
            other = region_input(index_offset=offset * 20)
            value["triangles"].extend(other["triangles"])
            value["pairs"] += tuple(other["pairs"])
        started = time.perf_counter(); batch = solve_region_candidates(value)
        samples.append((count, time.perf_counter() - started, batch.region_count))
    assert [row[2] for row in samples] == [1, 3, 6]
    assert samples[-1][1] < 2.0
