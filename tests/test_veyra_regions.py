from cloth_next.veyra.regions import (
    RegionCandidate, RegionDisplacement, RegionTriangle, _candidate_metrics,
    _edge_adjacency, _patch_crossings, _patch_weights, _validation_schedule,
    build_regions, clear_topology_cache, expand_patch,
    solve_region_candidates)


def triangle(index, vertices, points):
    return {"object_uuid": "cloth", "triangle_index": index,
            "vertex_indices": vertices, "vertices": points}


def two_sheet_value():
    rows = [
        triangle(0, (0, 1, 2), ((0, 0, 0), (1, 0, 0), (0, 1, 0))),
        triangle(1, (1, 3, 2), ((1, 0, 0), (1, 1, 0), (0, 1, 0))),
        triangle(2, (10, 11, 12), ((0, 0, .1), (1, 0, .1), (0, 1, .1))),
        triangle(3, (11, 13, 12), ((1, 0, .1), (1, 1, .1), (0, 1, .1))),
    ]
    return {"schema": "cnx.veyra.region-input.v1", "job_id": "job",
            "source_snapshot_identity": "a" * 64,
            "authoritative_total": 2, "detailed_count": 2,
            "mapped_count": 2, "pairs": ((0, 2), (1, 3)),
            "triangles": rows, "cumulative_displacements": [],
            "rejected_candidate_ids": []}


def test_two_independent_pair_groups_build_two_regions():
    value = two_sheet_value()
    offset = []
    for row in value["triangles"]:
        copy = dict(row)
        copy["triangle_index"] += 10
        copy["vertex_indices"] = tuple(index + 100
                                       for index in row["vertex_indices"])
        copy["vertices"] = tuple((x + 10, y, z)
                                 for x, y, z in row["vertices"])
        offset.append(copy)
    value["triangles"].extend(offset)
    value["pairs"] = (*value["pairs"], (10, 12), (11, 13))
    assert len(build_regions(value).regions) == 2


def test_connected_pair_chain_builds_one_region():
    assert len(build_regions(two_sheet_value()).regions) == 1


def test_vertex_ids_are_scoped_to_source_object_identity():
    value = two_sheet_value()
    other = []
    for row in value["triangles"]:
        copy = dict(row)
        copy["object_uuid"] = "other-cloth"
        copy["triangle_index"] += 10
        copy["vertices"] = tuple((x + 10, y, z)
                                 for x, y, z in row["vertices"])
        other.append(copy)
    value["triangles"].extend(other)
    value["pairs"] = (*value["pairs"], (10, 12), (11, 13))
    analysis = build_regions(value)
    assert len(analysis.regions) == 2
    batch = solve_region_candidates(value)
    candidate = batch.candidates[0]
    expected = {}
    for row in value["triangles"]:
        if row["object_uuid"] == candidate.object_uuid:
            for index, point in zip(row["vertex_indices"], row["vertices"]):
                expected.setdefault(index, tuple(map(float, point)))
    assert all(item.original == expected[item.vertex_index]
               for item in candidate.displacements)


def test_patch_ring_expansion_is_bounded():
    rows = {
        0: RegionTriangle("cloth", 0, (0, 1, 2), ((0, 0, 0),) * 3),
        1: RegionTriangle("cloth", 1, (1, 3, 2), ((0, 0, 0),) * 3),
        2: RegionTriangle("cloth", 2, (3, 4, 2), ((0, 0, 0),) * 3),
        3: RegionTriangle("cloth", 3, (4, 5, 2), ((0, 0, 0),) * 3),
    }
    distances = expand_patch((0,), _edge_adjacency(rows), rings=2)
    assert distances == {0: 0, 1: 1, 2: 2}


def test_side_assignment_is_stable_and_ambiguous_pair_is_skipped():
    first = build_regions(two_sheet_value()).regions[0]
    second = build_regions(two_sheet_value()).regions[0]
    assert (first.side_a, first.side_b) == (second.side_a, second.side_b)
    assert not first.ambiguous_sides
    value = two_sheet_value()
    value["pairs"] = ((0, 1),)
    assert build_regions(value).regions[0].ambiguous_sides


def test_boundary_falloff_and_weight_field_are_smooth():
    rows = {
        row["triangle_index"]: RegionTriangle(
            row["object_uuid"], row["triangle_index"],
            tuple(row["vertex_indices"]), tuple(row["vertices"]))
        for row in two_sheet_value()["triangles"][:2]}
    distances = {0: 0, 1: 2}
    weights = _patch_weights(distances, rows, 2)
    assert weights[0] > weights[1] >= weights[3]
    assert weights[3] == 0.0
    assert max(weights.values()) - min(weights.values()) <= 1.0


def test_candidate_is_deterministic_and_respects_movement_budgets():
    first = solve_region_candidates(two_sheet_value())
    second = solve_region_candidates(two_sheet_value())
    assert tuple(item.to_dict() for item in first.candidates) == tuple(
        item.to_dict() for item in second.candidates)
    assert ({key: value for key, value in first.analysis.items()
             if key != "build_seconds"} ==
            {key: value for key, value in second.analysis.items()
             if key != "build_seconds"})
    assert all(item.max_displacement <= item.local_scale * .08 + 1e-12
               for item in first.candidates)
    cumulative = two_sheet_value()
    cumulative["cumulative_displacements"] = [
        {"object_uuid": "cloth", "vertex_index": index,
         "delta": (0.19, 0.0, 0.0)} for index in range(14)]
    limited = solve_region_candidates(cumulative)
    assert all(item.max_displacement <= item.local_scale * .08 + 1e-12
               for item in limited.candidates)


def test_local_new_crossing_is_detected_before_lumen():
    rows = {
        0: RegionTriangle("cloth", 0, (0, 1, 2),
                          ((0, 0, 0), (1, 0, 0), (0, 1, 0))),
        1: RegionTriangle("cloth", 1, (3, 4, 5),
                          ((0, 0, 1), (1, 0, 1), (0, 1, 1))),
    }
    planned = {3: (0, 0, -1), 4: (0, 0, -1), 5: (0, 0, -1)}
    before, after = _patch_crossings({0, 1}, rows, planned)
    assert after > before


def test_edge_collapse_is_rejected():
    row = RegionTriangle("cloth", 0, (0, 1, 2),
                         ((0, 0, 0), (1, 0, 0), (0, 1, 0)))
    assert _candidate_metrics({1: (-1, 0, 0)}, {0}, {0: row}) is None


def test_compact_iteration_reuses_topology_and_refreshes_geometry():
    full = two_sheet_value()
    first = solve_region_candidates(full)
    rows = full.pop("triangles")
    full["topology_key"] = first.analysis.get("topology_key", "")
    # The public input normally carries the key computed by Blender. Obtain it
    # from the same immutable rows here and send only unique positions.
    from cloth_next.veyra.regions import topology_key
    full["topology_key"] = topology_key(rows)
    positions = {}
    for row in rows:
        for index, point in zip(row["vertex_indices"], row["vertices"]):
            positions[(row["object_uuid"], index)] = point
    positions[("cloth", 10)] = (0.0, 0.0, 0.2)
    full["vertex_positions"] = [
        {"object_uuid": key[0], "vertex_index": key[1], "position": point}
        for key, point in sorted(positions.items())]
    second = solve_region_candidates(full)
    assert second.candidates
    assert any(operation.original == (0.0, 0.0, 0.2)
               for candidate in second.candidates
               for operation in candidate.displacements
               if operation.vertex_index == 10)
    clear_topology_cache("job")


def test_adaptive_strength_uses_smallest_deformation_when_effect_is_equal():
    batch = solve_region_candidates(two_sheet_value())
    leaves = [item for item in batch.candidates
              if not item.member_candidate_ids]
    by_direction = {}
    for item in leaves:
        key = (item.region_id, item.direction_kind)
        assert key not in by_direction
        by_direction[key] = item.amplitude_fraction
    assert set(by_direction.values()) == {0.01}


def _rank_candidate(name, vertex, triangle):
    return RegionCandidate(
        name, vertex, "cloth",
        (RegionDisplacement(vertex, (0.0, 0.0, 0.0), (.01, 0.0, 0.0)),),
        1.0, .01, "test", .01, 1.0, 1.0, 1.0, 1.0, 0, 0,
        (triangle,), (), float(10 - vertex))


def test_independent_candidates_are_batched_and_binary_split_stably():
    candidates = tuple(_rank_candidate(str(index), index, index)
                       for index in range(4))
    schedule = _validation_schedule(candidates)
    assert schedule[0].member_candidate_ids == ("0", "1", "2", "3")
    assert [item.member_candidate_ids for item in schedule[1:3]] == [
        ("0", "1"), ("2", "3")]
    assert tuple(item.candidate_id for item in schedule[-4:]) == (
        "0", "1", "2", "3")


def test_overlapping_candidates_are_never_batched():
    first = _rank_candidate("first", 1, 1)
    second = _rank_candidate("second", 1, 2)
    schedule = _validation_schedule((first, second))
    assert all(not item.member_candidate_ids for item in schedule)
