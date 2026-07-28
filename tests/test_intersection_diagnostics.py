# SPDX-License-Identifier: GPL-3.0-or-later

from dataclasses import dataclass

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


def test_unmatched_legacy_solver_geometry_is_not_guessed():
    snapshot = diagnostics.build_solver_input_snapshot((
        (_object("cloth", "Skirt"), "CLOTH", None, False),),
        bake_start_frame=1)

    assert diagnostics.convert_violation({
        "type": "self_intersection",
        "tris": [[(20, 0, 0), (21, 0, 0), (20, 1, 0)]],
    }, snapshot) is None


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
