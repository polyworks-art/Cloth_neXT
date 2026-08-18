# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass
import sys
from types import SimpleNamespace

import pytest

from cloth_next import intersection_diagnostics as diagnostics
from cloth_next.blender import intersection_overlay


IDENTITY = (
    (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))


@dataclass(frozen=True)
class SceneObject:
    uuid: str
    name: str
    vertices_local: tuple
    triangles: tuple
    transform: tuple = IDENTITY


def _object(uuid, name, offset=0):
    return SceneObject(
        uuid, name,
        ((offset, 0, 0), (offset + 1, 0, 0), (offset, 1, 0)),
        ((0, 1, 2),))


def test_combined_mapping_preserves_static_collider_side_and_source_face():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Skirt"), "CLOTH", (32,), False),
        (_object("collider", "Character Collision Proxy", 2), "COLLIDER",
         (70,), True)), bake_start_frame=8)

    violation = diagnostics.convert_violation(
        {"combined_pair": [0, 1]}, snapshot)

    assert [e.object_name for e in violation.elements] == [
        "Skirt", "Character Collision Proxy"]
    assert [e.source_polygon_index for e in violation.elements] == [32, 70]
    assert violation.classification == "INITIAL_COLLIDER_PENETRATION"
    assert violation.elements[1].generated_proxy
    assert violation.elements[1].vertices == ((2.0, 0.0, 0.0),
                                               (3.0, 0.0, 0.0),
                                               (2.0, 1.0, 0.0))


def test_classifies_two_deformables_and_same_object():
    separate = diagnostics.build_solver_input_snapshot((
        (_object("cape", "Cape"), "CLOTH", None, False),
        (_object("coat", "Coat"), "SOFT_BODY", None, False)),
        bake_start_frame=1)
    assert diagnostics.convert_violation(
        {"pair": [0, 1]}, separate).classification == "DEFORMABLE_INTERSECTION"
    same = diagnostics.build_solver_input_snapshot((
        (SceneObject("cape", "Cape",
                     ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)),
                     ((0, 1, 2), (1, 3, 2))),
         "CLOTH", None, False),), bake_start_frame=1)
    assert diagnostics.convert_violation(
        {"pair": [0, 1]}, same).classification == "SELF_INTERSECTION"


def test_coplanar_is_detection_method_and_rod_is_primary_classification():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("rope", "Rope"), "ROD", None, False),
        (_object("body", "Body Collider"), "COLLIDER", None, False)),
        bake_start_frame=1)
    violation = diagnostics.convert_violation({
        "pair": [0, 1], "is_rod": True,
        "detection_method": "COPLANAR_OVERLAP"}, snapshot)
    assert violation.classification == "ROD_TRIANGLE_INTERSECTION"
    assert violation.detection_method == "COPLANAR_OVERLAP"


def test_internal_static_sentinel_is_not_presented_one_sided():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Cloth"), "CLOTH", None, False),
        (_object("cloth-next-internal-static-0", "Sentinel"), "COLLIDER",
         None, False)), bake_start_frame=1)
    assert diagnostics.convert_violation(
        {"pair": [0, 1]}, snapshot) is None


def test_legacy_solver_triangle_geometry_maps_to_visible_face():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Skirt"), "CLOTH", (32,), False),
        (_object("collider", "Body", 2), "COLLIDER", (70,), False)),
        bake_start_frame=1)

    violation = diagnostics.convert_violation({
        "type": "self_intersection",
        "tris": [[(1, 0, 0), (0, 1, 0), (0, 0, 0)]],
    }, snapshot)

    assert violation is not None
    assert violation.combined_pair == (0, -1)
    assert len(violation.elements) == 1
    assert violation.elements[0].object_name == "Skirt"
    assert violation.elements[0].source_polygon_index == 32


def test_solver_reindexed_pair_maps_by_authoritative_triangle_geometry():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth-a", "Cloth A"), "CLOTH", (32,), False),
        (_object("cloth-b", "Cloth B", 2), "CLOTH", (70,), False),
        (_object("cloth-c", "Cloth C", 4), "CLOTH", (90,), False)),
        bake_start_frame=1)

    violation = diagnostics.convert_violation({
        # Both values are in range but refer to different faces in the
        # solver's post-decode combined mesh.
        "combined_pair": [0, 1],
        "tris": [
            list(snapshot.triangles[1].vertices),
            list(snapshot.triangles[2].vertices),
        ],
    }, snapshot)

    assert violation is not None
    assert violation.combined_pair == (1, 2)
    assert [item.source_polygon_index for item in violation.elements] == [70, 90]


def test_unmatched_legacy_solver_geometry_is_not_guessed():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Skirt"), "CLOTH", None, False),),
        bake_start_frame=1)

    assert diagnostics.convert_violation({
        "type": "self_intersection",
        "tris": [[(20, 0, 0), (21, 0, 0), (20, 1, 0)]],
    }, snapshot) is None


def test_diagnostic_result_preserves_mapped_unmapped_and_solver_total():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Skirt"), "CLOTH", (32,), False),),
        bake_start_frame=1)

    result = diagnostics.map_diagnostics((
        {"combined_pair": [0, 0]},
        {"combined_pair": [0, 90]},
        {"tris": [[(20, 0, 0), (21, 0, 0), (20, 1, 0)]]},
    ), snapshot, detected_count=18)

    assert result.snapshot is snapshot
    assert result.detected_count == 18
    assert result.mapped_count == 1
    assert result.unmapped_count == 17
    assert result.total_count == 18
    assert result.mapped_violations == result.violations
    assert result.solver_input_snapshot is snapshot
    assert result.mapping_warning == (
        "17 solver-reported intersections could not be mapped safely.")
    assert result.unattributed_count == 15
    assert [item.reason for item in result.unmapped] == [
        "OUT_OF_RANGE_PAIR", "UNMATCHED_TRIANGLE_GEOMETRY"]
    assert result.unmapped[0].combined_pair == (0, 90)
    assert result.violations[0].total_count == 18
    assert result.self_intersections == result.violations
    assert result.has_intersections


def test_diagnostic_result_keeps_non_self_intersections_out_of_self_subset():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Skirt"), "CLOTH", None, False),
        (_object("collider", "Body", 2), "COLLIDER", None, False)),
        bake_start_frame=1)

    result = diagnostics.map_diagnostics(
        ({"combined_pair": [0, 1]},), snapshot)

    assert result.mapped_count == 1
    assert result.self_intersections == ()


def test_degenerate_faces_are_attributed_deterministically():
    cloth = SceneObject(
        "cloth", "Skirt",
        ((0, 0, 0), (1, 0, 0), (0, 1, 0), (2, 0, 0)),
        ((0, 1, 2), (1, 3, 2)))
    snapshot = diagnostics.build_solver_input_snapshot((
        (cloth, "CLOTH", (30, 31), False),), bake_start_frame=1)

    faces = diagnostics.degenerate_faces_from_combined_indices(
        snapshot, (1, 1, 99))
    result = diagnostics.map_diagnostics(
        (), snapshot, degenerate_faces=faces)

    assert not result.has_intersections
    assert result.has_degenerate_faces
    assert len(result.degenerate_faces) == 1
    face = result.degenerate_faces[0]
    assert face.object_name == "Skirt"
    assert face.combined_triangle_index == 1
    assert face.local_triangle_index == 1
    assert face.source_polygon_index == 31
    assert face.vertex_indices == (1, 3, 2)
    assert face.vertices == snapshot.triangles[1].vertices


def test_diagnostic_result_rejects_inconsistent_counts():
    with pytest.raises(ValueError, match="must equal"):
        diagnostics.DiagnosticResult(
            violations=(), detected_count=2, unattributed_count=1)


def test_legacy_local_solver_geometry_maps_through_object_transform():
    translated = SceneObject(
        "cloth", "Translated Cloth",
        ((0, 0, 0), (1, 0, 0), (0, 1, 0)), ((0, 1, 2),),
        ((1, 0, 0, 20), (0, 1, 0, 3), (0, 0, 1, -4),
         (0, 0, 0, 1)))
    snapshot = diagnostics.build_solver_input_snapshot(
        ((translated, "CLOTH", (9,), False),), bake_start_frame=1)

    violation = diagnostics.convert_violation({
        "type": "self_intersection",
        "tris": [[(0, 0, 0), (1, 0, 0), (0, 1, 0)]],
    }, snapshot)

    assert violation is not None
    assert violation.elements[0].vertices == (
        (20.0, 3.0, -4.0), (21.0, 3.0, -4.0), (20.0, 4.0, -4.0))


def test_strict_triangle_crossing_locator_rejects_separated_and_shared_edge():
    flat = ((0, 0, 0), (2, 0, 0), (0, 2, 0))
    crossing = ((0.5, 0.5, -1), (0.5, 0.5, 1), (1.5, 0.5, 0))
    separated = ((3, 0, 0), (4, 0, 0), (3, 1, 0))
    adjacent = ((0, 0, 0), (2, 0, 0), (1, -1, 0))

    assert diagnostics.triangles_strictly_cross(flat, crossing)
    assert not diagnostics.triangles_strictly_cross(flat, separated)
    assert not diagnostics.triangles_strictly_cross(flat, adjacent)


def test_coplanar_locator_detects_overlap_and_duplicates_not_shared_edge():
    first = ((0, 0, 0), (2, 0, 0), (0, 2, 0))
    overlap = ((0.5, -0.25, 0), (1.5, 0.75, 0), (0.5, 1.5, 0))
    adjacent = ((0, 0, 0), (2, 0, 0), (1, -1, 0))

    assert diagnostics.triangles_coplanar_overlap(first, overlap)
    assert diagnostics.triangles_coplanar_overlap(
        first, tuple(reversed(first)))
    assert not diagnostics.triangles_coplanar_overlap(first, adjacent)


def test_overlay_navigation_clear_and_solver_input_reuses_snapshot(monkeypatch):
    monkeypatch.setattr(intersection_overlay, "_ensure_handler", lambda: None)
    monkeypatch.setattr(intersection_overlay, "_redraw", lambda: None)
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("a", "A"), "CLOTH", None, False),
        (_object("b", "B"), "COLLIDER", None, False)),
        bake_start_frame=4)
    first = diagnostics.convert_violation(
        {"pair": [0, 1]}, snapshot, total_count=2)
    second = diagnostics.convert_violation(
        {"pair": [1, 0]}, snapshot, total_count=2)

    intersection_overlay.set_violations((first, second), snapshot)
    assert intersection_overlay.current() is first
    assert intersection_overlay.label_lines() == (
        "Initial Collider Penetration", "A · Triangle 0",
        "B · Triangle 0", "1 of 2")
    assert intersection_overlay.next_violation() is second
    assert intersection_overlay.previous_violation() is first
    assert intersection_overlay.solver_input_snapshot() is snapshot
    assert intersection_overlay.toggle_solver_input()
    intersection_overlay.clear()
    assert intersection_overlay.current() is None
    assert intersection_overlay.solver_input_snapshot() is None


def test_overlay_clear_removes_handlers_once_and_fully_resets(monkeypatch):
    removed = []
    space = SimpleNamespace(
        draw_handler_remove=lambda handle, region: removed.append(
            (handle, region)))
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(
        types=SimpleNamespace(SpaceView3D=space)))
    monkeypatch.setattr(intersection_overlay, "_redraw", lambda: None)
    monkeypatch.setattr(intersection_overlay, "_draw_handle", "geometry")
    monkeypatch.setattr(intersection_overlay, "_label_handle", "label")
    monkeypatch.setattr(intersection_overlay, "_show_input", True)

    intersection_overlay.clear()
    intersection_overlay.clear()

    assert removed == [("geometry", "WINDOW"), ("label", "WINDOW")]
    assert intersection_overlay.violations() == ()
    assert intersection_overlay.current_index() == 0
    assert not intersection_overlay.solver_input_visible()
    assert intersection_overlay.solver_input_snapshot() is None
    assert intersection_overlay._draw_handle is None
    assert intersection_overlay._label_handle is None


def test_overlay_set_clear_set_reinstalls_fresh_handlers(monkeypatch):
    added = []
    removed = []

    def add(callback, args, region, phase):
        handle = f"handle-{len(added)}"
        added.append((handle, callback, args, region, phase))
        return handle

    space = SimpleNamespace(
        draw_handler_add=add,
        draw_handler_remove=lambda handle, region: removed.append(
            (handle, region)))
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(
        types=SimpleNamespace(SpaceView3D=space),
        context=SimpleNamespace(window_manager=SimpleNamespace(windows=()))))
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Cloth"), "CLOTH", None, False),),
        bake_start_frame=1)
    violation = diagnostics.convert_violation(
        {"pair": [0, 0]}, snapshot, total_count=1)

    intersection_overlay.clear()
    intersection_overlay.set_violations((violation,), snapshot)
    first_handles = (intersection_overlay._draw_handle,
                     intersection_overlay._label_handle)
    intersection_overlay.clear()
    intersection_overlay.set_violations((violation,), snapshot)

    assert len(added) == 4
    assert removed == [(first_handles[0], "WINDOW"),
                       (first_handles[1], "WINDOW")]
    assert intersection_overlay.current() is violation
    assert intersection_overlay._draw_handle not in first_handles
    assert intersection_overlay._label_handle not in first_handles
    intersection_overlay.clear()


def test_partial_handler_install_is_rolled_back(monkeypatch):
    removed = []
    calls = 0

    def add(_callback, _args, _region, _phase):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("label handler unavailable")
        return "geometry"

    space = SimpleNamespace(
        draw_handler_add=add,
        draw_handler_remove=lambda handle, region: removed.append(
            (handle, region)))
    monkeypatch.setitem(sys.modules, "bpy", SimpleNamespace(
        types=SimpleNamespace(SpaceView3D=space)))
    monkeypatch.setattr(intersection_overlay, "_draw_handle", None)
    monkeypatch.setattr(intersection_overlay, "_label_handle", None)
    monkeypatch.setattr(intersection_overlay, "_violations", (object(),))

    intersection_overlay._ensure_handler()

    assert removed == [("geometry", "WINDOW")]
    assert intersection_overlay._draw_handle is None
    assert intersection_overlay._label_handle is None


def test_nonzero_detected_count_with_mapping_has_drawable_geometry(monkeypatch):
    monkeypatch.setattr(intersection_overlay, "_ensure_handler", lambda: None)
    monkeypatch.setattr(intersection_overlay, "_redraw", lambda: None)
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Cloth"), "CLOTH", None, False),),
        bake_start_frame=1)
    violation = diagnostics.convert_violation(
        {"pair": [0, 0]}, snapshot, total_count=18)

    intersection_overlay.set_violations((violation,), snapshot)

    triangles = intersection_overlay._triangles_for_draw()
    assert len(triangles) == 2
    assert all(len(vertices) == 3 for vertices, _color in triangles)
    assert "18 detected · 1 mapped" in intersection_overlay.label_lines()
    intersection_overlay.clear()


def test_overlay_authoritative_session_presents_unmapped_and_degenerate(
        monkeypatch):
    monkeypatch.setattr(intersection_overlay, "_ensure_handler", lambda: None)
    monkeypatch.setattr(intersection_overlay, "_redraw", lambda: None)
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Cloth"), "CLOTH", (4,), False),),
        bake_start_frame=1)
    violation = diagnostics.convert_violation(
        {"pair": [0, 0]}, snapshot, total_count=2)
    degenerate = diagnostics.DegenerateFace(
        object_uuid="cloth", object_name="Cloth", role="CLOTH",
        combined_triangle_index=0, local_triangle_index=3,
        source_polygon_index=12, vertex_indices=(0, 1, 2),
        vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                  (2.0, 0.0, 0.0)))
    session = diagnostics.DiagnosticResult(
        snapshot=snapshot, violations=(violation,),
        unmapped=(diagnostics.UnmappedIntersection(
            ordinal=2, reason="outside retained input"),),
        detected_count=2, degenerate_faces=(degenerate,))

    intersection_overlay.set_diagnostic_session(session)

    assert intersection_overlay.diagnostic_session() is session
    assert intersection_overlay.detected_count() == 2
    assert intersection_overlay.mapped_count() == 1
    assert intersection_overlay.solver_input_snapshot() is snapshot
    assert intersection_overlay.current() is violation
    assert "2 detected · 1 mapped" in intersection_overlay.label_lines()
    assert "could not be mapped safely" in intersection_overlay.mapping_warning()

    assert intersection_overlay.next_violation() is degenerate
    assert intersection_overlay.label_lines()[:3] == (
        "Degenerate Face", "Cloth · Triangle 12",
        "Degenerate face 1 of 1")
    primitives = intersection_overlay.primitives_for_diagnostic(degenerate)
    assert [primitive.mode for primitive in primitives] == [
        "TRIS", "LINES", "POINTS"]
    assert primitives[-1].point_size == 7.0

    intersection_overlay.clear()
    assert intersection_overlay.diagnostic_session() is None
    assert intersection_overlay.presentation_diagnostics() == ()
    assert intersection_overlay.detected_count() == 0


def test_self_intersection_primitive_generation_is_pure_and_deterministic():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Cloth"), "CLOTH", None, False),),
        bake_start_frame=1)
    violation = diagnostics.convert_violation(
        {"pair": [0, 0]}, snapshot, total_count=1)

    first = intersection_overlay.primitives_for_diagnostic(violation)
    second = intersection_overlay.primitives_for_diagnostic(violation)

    assert first == second
    assert [primitive.mode for primitive in first] == [
        "TRIS", "LINES", "TRIS", "LINES"]
    assert first[0].vertices == ((0.0, 0.0, 0.0),
                                 (1.0, 0.0, 0.0),
                                 (0.0, 0.0, 1.0))
