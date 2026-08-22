"""Reusable geometry factories for VEYRA generalization tests."""

from __future__ import annotations

import math

from cloth_next.veyra.weld import WeldVertex


def transform(point, *, scale=1.0, angle=0.0, offset=(0.0, 0.0, 0.0)):
    x, y, z = point
    cosine, sine = math.cos(angle), math.sin(angle)
    return (scale * (cosine * x - sine * y) + offset[0],
            scale * (sine * x + cosine * y) + offset[1],
            scale * z + offset[2])


def boundary_seam(*, stacked=False, separation=0.0, scale=1.0, angle=0.0,
                  offset=(0.0, 0.0, 0.0), attributes=("fabric",),
                  permutation=None):
    """Two two-triangle patches whose duplicate boundary is or is not continuous."""
    points = {
        0: (0.0, 0.0, 0.0), 2: (0.0, 1.0, 0.0),
        1: (separation, 0.0, 0.0), 3: (separation, 1.0, 0.0),
        4: (-1.0, 0.0, 0.0), 5: (-1.0, 1.0, 0.0),
        6: ((-1.0 if stacked else 1.0), 0.0, 0.0),
        7: ((-1.0 if stacked else 1.0), 1.0, 0.0),
    }
    adjacency = {
        0: (2, 4), 2: (0, 5), 4: (0, 5), 5: (2, 4),
        1: (3, 6), 3: (1, 7), 6: (1, 7), 7: (3, 6),
    }
    mapping = permutation or {index: index for index in points}
    values = []
    for old in sorted(points):
        index = mapping[old]
        values.append(WeldVertex(
            index, transform(points[old], scale=scale, angle=angle, offset=offset),
            0 if old in {0, 2, 4, 5} else 1,
            old in {0, 1, 2, 3}, attributes,
            frozenset(mapping[item] for item in adjacency[old]), frozenset()))
    return tuple(values)


def region_input(*, object_uuid="cloth", scale=1.0, offset=(0.0, 0.0, 0.0),
                 angle=0.0, face_order=(0, 1, 2, 3), index_offset=0):
    raw = (
        ((0, 1, 2), ((0, 0, 0), (1, 0, 0), (0, 1, 0))),
        ((1, 3, 2), ((1, 0, 0), (1, 1, 0), (0, 1, 0))),
        ((10, 11, 12), ((0, 0, .1), (1, 0, .1), (0, 1, .1))),
        ((11, 13, 12), ((1, 0, .1), (1, 1, .1), (0, 1, .1))),
    )
    rows = []
    for new_triangle, old_triangle in enumerate(face_order):
        vertices, points = raw[old_triangle]
        rows.append({
            "object_uuid": object_uuid,
            "triangle_index": new_triangle + index_offset,
            "vertex_indices": tuple(index + index_offset * 10 for index in vertices),
            "vertices": tuple(transform(point, scale=scale, angle=angle,
                                         offset=offset) for point in points),
        })
    inverse = {old: new + index_offset for new, old in enumerate(face_order)}
    return {
        "schema": "cnx.veyra.region-input.v1", "job_id": f"job-{index_offset}",
        "source_snapshot_identity": "a" * 64, "authoritative_total": 2,
        "detailed_count": 2, "mapped_count": 2,
        "pairs": ((inverse[0], inverse[2]), (inverse[1], inverse[3])),
        "triangles": rows, "cumulative_displacements": [],
        "rejected_candidate_ids": [],
    }
