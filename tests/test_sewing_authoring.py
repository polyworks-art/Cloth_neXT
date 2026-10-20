# SPDX-License-Identifier: GPL-3.0-or-later
"""Picking, routing, toolbar layout and pixel overlays of the Sewing editor."""
import math

import numpy as np
import pytest

from cloth_next import sewing_pick, tool_icons, tool_overlay
from cloth_next.blender.sewing_overlay import SeamGeometry, pixel_segments, quantized_count
from cloth_next.sewing_visual import segment


def grid(n):
    vertices = [(x / (n - 1), y / (n - 1), 0.0) for y in range(n) for x in range(n)]
    edges = []
    polygons = []
    for y in range(n):
        for x in range(n):
            i = y * n + x
            if x + 1 < n:
                edges.append((i, i + 1))
            if y + 1 < n:
                edges.append((i, i + n))
            if x + 1 < n and y + 1 < n:
                polygons.append((i, i + 1, i + n + 1, i + n))
    return vertices, edges, polygons


class View:
    def __init__(self, width=400, height=300):
        # Orthographic top view of the unit square, scaled into the region.
        self.width, self.height = width, height
        self.matrix = np.array(((2.0, 0, 0, -1.0), (0, 2.0, 0, -1.0),
                                (0, 0, 1.0, 0), (0, 0, 0, 1.0)))


def test_projection_matches_region_convention():
    view = View()
    points = sewing_pick.project(view.matrix, [(0, 0, 0), (1, 1, 0), (.5, .25, 0)],
                                 view.width, view.height)
    assert points.tolist() == [[0, 0], [400, 300], [200, 75]]


def test_points_behind_the_view_are_not_pickable():
    matrix = np.eye(4)
    matrix[3] = (0, 0, -1, 0)                 # w = -z: z > 0 lies behind the eye
    points = sewing_pick.project(matrix, [(0, 0, -1), (0, 0, 1)], 100, 100)
    assert not np.isnan(points[0]).any() and np.isnan(points[1]).all()


def test_edge_picking_returns_nearest_edges_within_radius():
    vertices, edges, polygons = grid(5)
    view = View()
    projected = sewing_pick.project(view.matrix, vertices, view.width, view.height)
    boundary = sewing_pick.boundary_edges(polygons)
    edge_projection = sewing_pick.EdgeProjection(projected, boundary)
    # Near the bottom boundary between x=.25 and x=.5.
    hits = edge_projection.nearest((150, 4))
    assert hits and hits[0][1] == (1, 2) and hits[0][0] == pytest.approx(16)
    assert not edge_projection.nearest((200, 150))      # interior: no boundary edge
    assert len(boundary) == 16


def test_dense_mesh_picking_is_vectorised():
    vertices, edges, polygons = grid(301)                # 90,601 vertices
    view = View(1600, 900)
    projected = sewing_pick.project(view.matrix, vertices, view.width, view.height)
    edge_projection = sewing_pick.EdgeProjection(projected, edges)
    import time
    start = time.perf_counter()
    for _ in range(20):
        hits = edge_projection.nearest((801.3, 451.7))
    elapsed = (time.perf_counter() - start) / 20
    assert hits
    assert elapsed < .05


def test_nearest_vertex_snapping_and_tie_break():
    view = View()
    projected = sewing_pick.project(view.matrix, [(0, 0, 0), (.01, 0, 0), (1, 1, 0)],
                                    view.width, view.height)
    assert sewing_pick.nearest_vertices(projected, None, (1, 0))[0][1] == 0
    assert sewing_pick.nearest_vertices(projected, {1, 2}, (1, 0))[0][1] == 1
    assert sewing_pick.nearest_vertices(projected, None, (300, 300)) == ()


def test_free_route_keeps_explicit_points_and_forbids_reuse():
    vertices, edges, _ = grid(4)
    neighbours = sewing_pick.adjacency(len(vertices), edges)
    first = sewing_pick.route(vertices, neighbours, 0, 5)
    assert first[0] == 0 and first[-1] == 5 and len(first) == 3
    # Continuing to 3 may not walk back through the path's own vertices.
    second = sewing_pick.route(vertices, neighbours, 5, 3, forbidden=frozenset(first[:-1]))
    assert second[0] == 5 and second[-1] == 3
    assert not set(second[1:]) & set(first)
    assert sewing_pick.route(vertices, neighbours, 0, 0) == (0,)


def test_route_reports_unreachable_points():
    vertices = [(0, 0, 0), (1, 0, 0), (5, 0, 0)]
    neighbours = sewing_pick.adjacency(3, [(0, 1)])
    assert sewing_pick.route(vertices, neighbours, 0, 2) is None


def test_segment_stops_at_corners_with_prebuilt_adjacency():
    vertices, _edges, polygons = grid(5)
    boundary = sewing_pick.boundary_edges(polygons)
    neighbours = sewing_pick.adjacency(len(vertices), boundary)
    path = segment(vertices, (), (1, 2), neighbours=neighbours)
    assert set(path) == {0, 1, 2, 3, 4}
    assert path == segment(vertices, boundary, (1, 2))


def test_polyline_distance():
    points = [(0, 0), (10, 0), (10, 10)]
    assert sewing_pick.polyline_distance(points, (5, 3)) == pytest.approx(3)
    assert sewing_pick.polyline_distance([(0, 0)], (5, 3)) == math.inf


def _measure(label, factor):
    return len(label) * 6.0 * factor


def test_toolbar_column_hangs_below_navigation_and_hits_painted_buttons():
    items = [tool_overlay.ToolButton("a", "A", "mode", icon="segment"),
             tool_overlay.SEPARATOR, tool_overlay.SEPARATOR,
             (tool_overlay.ToolButton("b", "B"), tool_overlay.ToolButton("c", "C", enabled=False)),
             tool_overlay.ToolButton("v", "90", "value"),
             tool_overlay.SEPARATOR]
    top = tool_overlay.navigation_bottom(800, gizmo_size=80, ui_scale=1.0)
    center = tool_overlay.navigation_center(1200, gizmo_size=80, ui_scale=1.0)
    layout = tool_overlay.vertical_layout(items, center, top, 100, 1.0)
    assert center == pytest.approx(1176)
    assert top < 800 - 2.525 * 80                # below the perspective button
    assert layout.width == pytest.approx(tool_overlay.BUTTON)
    assert [p.button.identifier for p in layout.buttons] == ["a", "b", "c", "v"]
    assert len(layout.separators) == 1           # duplicate and trailing ones dropped
    ys = [p.y for p in layout.buttons]
    assert ys == sorted(ys, reverse=True)
    a, b, c, v = layout.buttons
    assert layout.hit(a.x + 12, a.y + 12) == "a"
    assert layout.hit(c.x + 12, c.y + 12) is None    # disabled
    assert layout.hit(v.x + 12, v.y + 12) is None    # value readout
    assert layout.hit(a.x - 30, a.y) is None
    assert layout.placed("b") is b


def test_toolbar_shrinks_uniformly_in_small_regions():
    items = [tool_overlay.ToolButton(str(i), str(i)) for i in range(10)]
    layout = tool_overlay.vertical_layout(items, 100, 300, 100, 1.0)
    assert layout.height <= 200 + 1e-6
    assert layout.scale < 1.0
    assert all(p.width == pytest.approx(layout.width) for p in layout.buttons)


def test_toolbar_without_navigation_gizmo_starts_at_the_top():
    assert tool_overlay.navigation_bottom(500, show_navigation=False) == pytest.approx(490)


def test_every_editor_icon_has_a_placeholder():
    names = {"segment", "free", "multi", "edit", "reverse", "back", "cancel", "confirm",
             "close", "delete", "threads", "xray", "enable", "minus", "plus", "attach",
             "smart", "path", "clear", "next"}
    for name in names:
        glyph = tool_icons.icon(name)
        assert glyph["strokes"] or glyph["dots"], name
        for stroke in glyph["strokes"]:
            assert len(stroke) >= 2
            assert all(-1.0 <= value <= 1.0 for point in stroke for value in point), name
    assert tool_icons.icon("unknown")["dots"]


def test_pixel_notches_and_markers_follow_direction():
    view = View()
    first = tuple((x / 10, .2, 0) for x in range(11))
    second = tuple((x / 10, .8, 0) for x in range(11))
    forward = pixel_segments(view, SeamGeometry("s", (1,), first, second))
    flipped = pixel_segments(view, SeamGeometry("s", (1,), first, second, flipped=True))
    assert forward.shape == flipped.shape and len(forward) % 2 == 0
    # 2 sides x 4 notches x 2 wings, then end rings.
    notch = forward[:32].reshape(-1, 2, 2)
    tips_second = notch[16:, 1, 0]
    tips_second_flipped = flipped[:32].reshape(-1, 2, 2)[16:, 1, 0]
    assert np.all(np.diff(tips_second[::2]) > 0)
    assert np.all(np.diff(tips_second_flipped[::2]) < 0)
    assert not len(pixel_segments(view, SeamGeometry("e", (1,), (), ()),
                                  notches=True, endpoints=True))


def test_thread_count_ladder_limits_rebuilds_while_zooming():
    counts = {quantized_count(pixels, 1.0) for pixels in range(400, 520)}
    assert len(counts) <= 3
    assert quantized_count(10, 1.0) == 2
