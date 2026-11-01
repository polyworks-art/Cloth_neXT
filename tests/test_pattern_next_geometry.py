# SPDX-License-Identifier: GPL-3.0-or-later
"""PATTERN NeXt geometry core (no Blender).

The mesher runs here with the pure fallback Delaunay; the add-on uses
Blender's exact constrained Delaunay, which ``tools/
blender_pattern_geometry_check.py`` validates in real Blender.
"""

import math
from dataclasses import replace

import pytest

from cloth_next.pattern_next import editing, quality
from cloth_next.pattern_next.curves import bezier, display_polyline, resample, segment_length
from cloth_next.pattern_next.delaunay import constrained
from cloth_next.pattern_next.mesher import mesh_shape
from cloth_next.pattern_next.seams import SeamError, SeamSide, side_segments, side_vertices
from cloth_next.pattern_next.shapes import (Contour, ContourPoint, PatternShape, ellipse_points,
                                            mirror_points, rectangle_points, signed_area,
                                            to_canvas, to_local)
from cloth_next.pattern_next.snapping import nice_grid_step, snap
from cloth_next.pattern_next.validation import PatternError, validate_ring, validate_shape


def poly(points):
    return tuple(ContourPoint(i + 1, co) for i, co in enumerate(points))


L_SHAPE = poly([(0, 0), (0.4, 0), (0.4, 0.1), (0.1, 0.1), (0.1, 0.4), (0, 0.4)])


@pytest.mark.parametrize("points", [rectangle_points(0.5, 0.4, range(1, 5)), L_SHAPE,
                                    poly([(0, 0), (0.5, 0), (0.2, 0.4)])])
def test_mesh_is_filled_conforming_and_interior(points):
    shape = PatternShape(Contour(points), spacing=0.05)
    result = mesh_shape(shape, constrained)
    report = quality.check(result, shape)
    assert quality.passed(report, shape.tolerance), report
    assert report["area_error_relative"] < 1e-9
    assert report["interior_vertices"] > 0  # not just the corner points
    assert report["min_angle"] > 20.0


def test_particle_distance_controls_density():
    counts = []
    for spacing in (0.1, 0.05, 0.025):
        shape = PatternShape(Contour(rectangle_points(0.5, 0.4, range(1, 5))), spacing=spacing)
        counts.append(len(mesh_shape(shape, constrained).triangles))
    assert counts[0] < counts[1] < counts[2]
    assert counts[2] > 3 * counts[1]


def test_curved_boundary_stays_within_tolerance():
    contour = Contour(ellipse_points(0.3, 0.2, range(1, 5)))
    samples = resample(contour, 0.05, 0.001)
    dense = display_polyline(contour, 1e-6)
    ring = [s.co for s in samples]

    def distance_to_ring(p):
        best = math.inf
        for a, b in zip(ring, ring[1:] + ring[:1]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
            best = min(best, math.hypot(p[0] - a[0] - dx * t, p[1] - a[1] - dy * t))
        return best
    assert max(distance_to_ring(p) for p in dense) <= 0.001 * 1.01
    # ellipse area within the Bezier circle approximation and chord tolerance
    assert abs(abs(signed_area(ring)) - math.pi * 0.3 * 0.2) / (math.pi * 0.06) < 0.01


def test_resample_keeps_corners_and_stable_sources():
    samples = resample(Contour(L_SHAPE), 0.05, 0.001)
    corners = [s for s in samples if s.corner]
    assert [s.segment_uid for s in corners] == [1, 2, 3, 4, 5, 6]
    assert all(0.0 <= s.fraction < 1.0 for s in samples)
    lengths = [math.dist(a.co, b.co) for a, b in zip(samples, samples[1:] + samples[:1])]
    assert max(lengths) <= 0.05 + 1e-9


@pytest.mark.parametrize("points, code", [
    (poly([(0, 0), (0.4, 0.4), (0.4, 0), (0, 0.25)]), "SELF_INTERSECTION"),
    (poly([(0, 0), (0.4, 0), (0.4, 0), (0.2, 0.3)]), "DUPLICATE_POINT"),
    (poly([(0, 0), (0.4, 0), (0.8, 0)]), "ZERO_AREA"),
    (poly([(0, 0), (0.4, 0)]), "TOO_FEW_POINTS"),
    (poly([(0, 0), (0.4, 0), (0.2, 0.3), (0.2, 0.0)]), "SELF_INTERSECTION"),
])
def test_invalid_outlines_are_rejected(points, code):
    with pytest.raises(PatternError) as error:
        validate_ring([p.co for p in points])
    assert error.value.code == code


def test_open_contour_is_rejected():
    shape = PatternShape(Contour(rectangle_points(0.3, 0.3, range(4)), False))
    with pytest.raises(PatternError) as error:
        mesh_shape(shape, constrained)
    assert error.value.code == "OPEN_CONTOUR"


def test_holes_must_be_inside_and_separate():
    outer = [(0, 0), (1, 0), (1, 1), (0, 1)]
    with pytest.raises(PatternError, match="not inside"):
        validate_shape(outer, [[(2, 2), (2.2, 2), (2.2, 2.2)]])
    with pytest.raises(PatternError) as error:
        validate_shape(outer, [[(0.2, 0.2), (0.6, 0.2), (0.6, 0.6)],
                               [(0.25, 0.22), (0.3, 0.22), (0.3, 0.27)]])
    assert error.value.code == "HOLE_NESTED"


def test_hole_is_cut_out_of_the_mesh():
    hole = tuple(ContourPoint(10 + i, co) for i, co in enumerate(
        [(-0.1, -0.08), (0.1, -0.08), (0.1, 0.08), (-0.1, 0.08)]))
    shape = PatternShape(Contour(rectangle_points(0.6, 0.5, range(1, 5))), (Contour(hole),),
                         spacing=0.05)
    result = mesh_shape(shape, constrained)
    report = quality.check(result, shape)
    assert quality.passed(report, shape.tolerance), report
    assert abs(report["area_mesh"] - (0.3 - 0.032)) < 1e-9


def test_insert_point_keeps_the_curve_exact():
    points = ellipse_points(0.3, 0.2, range(1, 5))
    contour = Contour(points)
    p0, c0, c1, p1, _ = contour.segment(0)
    before = [bezier(p0, c0, c1, p1, t / 20) for t in range(21)]
    split = editing.insert_point(points, 0, 0.37, 99)
    assert len(split) == 5 and split[1].uid == 99
    # The two new Bezier halves trace exactly the original quarter arc.
    first, second = Contour(split).segment(0), Contour(split).segment(1)
    halves = [bezier(*first[:4], t / 40) for t in range(41)] + \
             [bezier(*second[:4], t / 40) for t in range(41)]
    assert math.dist(bezier(*first[:4], 1.0), bezier(p0, c0, c1, p1, 0.37)) < 1e-12
    for p in before:
        assert min(math.dist(p, q) for q in halves) < 5e-3
    assert max(min(math.dist(q, bezier(p0, c0, c1, p1, t / 400)) for t in range(401))
               for q in halves) < 1e-3


def test_mirror_keeps_uids_and_reverses_to_ccw():
    points = editing.make_curved(L_SHAPE, 2, bulge=0.2)
    mirrored = mirror_points(points)
    assert sorted(p.uid for p in mirrored) == sorted(p.uid for p in points)
    ring = display_polyline(Contour(mirrored))
    assert signed_area(ring) > 0
    original = display_polyline(Contour(points))
    assert abs(signed_area(ring) - signed_area(original)) < 1e-9
    assert abs(min(p[0] for p in ring) + max(p[0] for p in original)) < 1e-9


def test_edge_length_and_bend():
    points = editing.set_segment_length(L_SHAPE, 0, 0.55)
    assert abs(segment_length(Contour(points), 0) - 0.55) < 1e-9
    bent = editing.bend_segment(L_SHAPE, 0, 0.5, (0.2, -0.05))
    p0, c0, c1, p1, curved = Contour(bent).segment(0)
    assert curved and math.dist(bezier(p0, c0, c1, p1, 0.5), (0.2, -0.05)) < 1e-9


def test_seam_side_survives_remesh_and_point_insertion():
    shape = PatternShape(Contour(L_SHAPE), spacing=0.05)
    side = SeamSide("p", 1, 3)  # segments 1->2 and 2->3
    coarse = mesh_shape(shape, constrained)
    fine = mesh_shape(replace(shape, spacing=0.025), constrained)
    a, b = side_vertices(coarse.outline, side), side_vertices(fine.outline, side)
    assert len(b) > len(a) >= 3
    assert coarse.vertices[a[0]].tolist() == [0.0, 0.0]
    assert coarse.vertices[a[-1]].tolist() == pytest.approx([0.4, 0.1])
    inserted = editing.insert_point(L_SHAPE, 0, 0.5, 50)
    assert side_segments(inserted, side) == [0, 1, 2]
    with pytest.raises(SeamError):
        side_segments(editing.remove_point(L_SHAPE, 2), side)


def test_snapping_priority_and_angles():
    vertices = [((0.0, 0.0), "v")]
    lines = [([(-1.0, 0.5), (1.0, 0.5)], False, "e")]
    assert snap((0.004, 0.003), vertices=vertices, polylines=lines, radius=0.01,
                grid=0.1, use_grid=True).kind == "VERTEX"
    edge = snap((0.3, 0.505), vertices=vertices, polylines=lines, radius=0.01, grid=0.1,
                use_grid=True)
    assert edge.kind == "EDGE" and edge.co == pytest.approx((0.3, 0.5))
    grid = snap((0.31, 0.22), vertices=vertices, polylines=lines, radius=0.01, grid=0.1,
                use_grid=True)
    assert grid.kind == "GRID" and grid.co == pytest.approx((0.3, 0.2))
    angled = snap((1.0, 0.95), origin=(0.0, 0.0), constrain=True)
    assert angled.kind == "ANGLE" and angled.co[0] == pytest.approx(angled.co[1])
    assert nice_grid_step(0.001, 12) == pytest.approx(0.02)


def test_canvas_transform_round_trip():
    local = (0.12, -0.3)
    canvas = to_canvas(local, (1.0, 2.0), 0.7)
    assert to_local(canvas, (1.0, 2.0), 0.7) == pytest.approx(local)


def test_shape_key_changes_with_geometry_only():
    a = PatternShape(Contour(L_SHAPE), spacing=0.02, location=(0, 0))
    b = replace(a, location=(5, 5), rotation=1.0)
    assert a.key() == b.key()
    moved = editing.move_point(L_SHAPE, 1, (0.5, 0.0))
    assert PatternShape(Contour(moved), spacing=0.02).key() != a.key()
    assert replace(a, spacing=0.03).key() != a.key()
