# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from types import SimpleNamespace

from cloth_next.attachments import (
    AttachmentError, AttachmentPoint, ObjectAttachment,
    closest_point_on_triangle, closest_surface_point,
    selected_topology, shortest_mesh_path, spatial_vertex_mapping,
    topology_fingerprint, topology_vertex_mapping, wire_entry,
)
from cloth_next.ppf.adapters import ADAPTERS
from cloth_next.materials import ShellMaterialSettings
from cloth_next.materials.deformables import SoftBodyMaterialSettings
from cloth_next.ppf.schema.params import (
    SimulationSettings, build_multi_deformable_param_payload,
    encode_multi_deformable_param)
from cloth_next.ppf.schema import envelope


def test_spatial_mapping_single_and_empty_selection():
    vertices = ((0, 0, 0),)
    assert spatial_vertex_mapping(vertices, (0,), vertices, (0,)) == ((0, 0),)
    with pytest.raises(AttachmentError, match="Source vertex selection is empty"):
        spatial_vertex_mapping(vertices, (), vertices, (0,))
    with pytest.raises(AttachmentError, match="Target vertex selection is empty"):
        spatial_vertex_mapping(vertices, (0,), vertices, ())


def test_spatial_mapping_is_deterministic_and_click_order_independent():
    source = ((0, 0, 0), (10, 0, 0), (5, 0, 0))
    target = ((10.1, 0, 0), (.1, 0, 0), (5.1, 0, 0))
    expected = ((0, 1), (1, 0), (2, 2))
    assert spatial_vertex_mapping(source, (2, 0, 1), target, (2, 1, 0)) == expected
    assert spatial_vertex_mapping(source, (1, 2, 0), target, (0, 2, 1)) == expected


def test_spatial_mapping_unequal_counts_reuses_only_when_required():
    source = ((0, 0, 0), (2, 0, 0), (9, 0, 0))
    target = ((0, 0, 0), (10, 0, 0))
    mapping = spatial_vertex_mapping(source, (0, 1, 2), target, (0, 1))
    assert len(mapping) == 3
    assert len({source_index for source_index, _ in mapping}) == 3
    assert {target_index for _, target_index in mapping} == {0, 1}


def test_spatial_mapping_uses_supplied_world_space_positions():
    source_world = ((100, 0, 0), (110, 0, 0))
    target_world = ((109, 0, 0), (101, 0, 0))
    assert spatial_vertex_mapping(source_world, (0, 1), target_world, (0, 1)) == (
        (0, 1), (1, 0))


def test_ordered_parallel_paths_never_cross_with_random_target_indices():
    source = tuple((float(index), 0.0, 0.0) for index in range(5))
    source_edges = tuple((index, index + 1) for index in range(4))
    # Spatial order is 3, 1, 4, 0, 2; index order is deliberately unrelated.
    target = ((3.0, 1.0, 0.0), (1.0, 1.0, 0.0), (4.0, 1.0, 0.0),
              (0.0, 1.0, 0.0), (2.0, 1.0, 0.0))
    target_order = (3, 1, 4, 0, 2)
    target_edges = tuple(zip(target_order, target_order[1:]))
    mapping = topology_vertex_mapping(
        source, (4, 1, 3, 0, 2), source_edges,
        target, tuple(reversed(target_order)), target_edges)
    assert mapping == ((0, 3), (1, 1), (2, 4), (3, 0), (4, 2))
    ranks = {vertex: rank for rank, vertex in enumerate(target_order)}
    mapped_ranks = [ranks[target_index] for _, target_index in mapping]
    assert mapped_ranks == sorted(mapped_ranks)


def test_ordered_paths_choose_one_complete_lower_cost_orientation():
    source = tuple((float(index), 0.0, 0.0) for index in range(4))
    target = tuple((float(3 - index), 1.0, 0.0) for index in range(4))
    edges = ((0, 1), (1, 2), (2, 3))
    mapping = topology_vertex_mapping(
        source, (3, 2, 1, 0), edges, target, (0, 1, 2, 3), edges)
    assert mapping == ((0, 3), (1, 2), (2, 1), (3, 0))


def test_unequal_ordered_paths_are_monotone_by_normalized_arc_length():
    source = ((0.0, 0, 0), (1.0, 0, 0), (2.0, 0, 0), (3.0, 0, 0))
    target = tuple((index * .6, 1.0, 0.0) for index in range(6))
    source_edges = ((0, 1), (1, 2), (2, 3))
    target_edges = tuple((index, index + 1) for index in range(5))
    mapping = topology_vertex_mapping(
        source, range(4), source_edges, target, range(6), target_edges)
    target_indices = [target_index for _, target_index in mapping]
    assert target_indices == sorted(target_indices)
    assert target_indices == [0, 2, 3, 5]


def test_ordered_mapping_uses_transformed_world_positions_and_is_deterministic():
    source = ((10.0, 0, 0), (11.0, 0, 0), (12.0, 0, 0))
    target = ((12.0, 2, 0), (11.0, 2, 0), (10.0, 2, 0))
    edges = ((0, 1), (1, 2))
    first = topology_vertex_mapping(source, (2, 0, 1), edges,
                                    target, (1, 2, 0), edges)
    second = topology_vertex_mapping(source, (1, 2, 0), edges,
                                     target, (0, 1, 2), edges)
    assert first == second == ((0, 2), (1, 1), (2, 0))


def test_disconnected_and_branched_selections_use_spatial_fallback():
    vertices = ((0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0))
    disconnected = topology_vertex_mapping(
        vertices, range(4), ((0, 1), (2, 3)),
        vertices, range(4), ((0, 1),))
    assert disconnected == spatial_vertex_mapping(vertices, range(4), vertices, range(4))
    branched_edges = ((0, 1), (0, 2), (0, 3))
    branched = topology_vertex_mapping(
        vertices, range(4), branched_edges,
        vertices, range(4), branched_edges)
    assert branched == spatial_vertex_mapping(vertices, range(4), vertices, range(4))


def test_loop_selection_is_rejected_instead_of_randomly_broken():
    vertices = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))
    loop = ((0, 1), (1, 2), (2, 3), (3, 0))
    assert selected_topology(vertices, range(4), loop).kind == "LOOP"
    with pytest.raises(AttachmentError, match="Closed-loop"):
        topology_vertex_mapping(vertices, range(4), loop,
                                vertices, range(4), loop)


def test_path_select_uses_original_edges_and_deterministic_shortest_route():
    vertices = ((0, 0, 0), (1, 0, 0), (2, 0, 0),
                (0, 2, 0), (1, 2, 0), (2, 2, 0))
    edges = ((0, 1), (1, 2), (0, 3), (3, 4), (4, 5), (5, 2))
    assert shortest_mesh_path(vertices, edges, 0, 2) == (0, 1, 2)
    with pytest.raises(AttachmentError, match="disconnected"):
        shortest_mesh_path(vertices, ((0, 1),), 0, 5)


def _attachment(source_role="CLOTH", target_role="SOFT_BODY"):
    return ObjectAttachment(
        "relation", "Sleeve to Body", "source-uuid", "target-uuid",
        source_role, target_role, 1.0,
        (AttachmentPoint(2, (0, 1, 2), (0.2, 0.3, 0.5),
                         (1.0, 2.0, 3.0), (4.0, 5.0, 6.0)),))


@pytest.mark.parametrize("source_role,target_role", [
    ("CLOTH", "CLOTH"), ("CLOTH", "SOFT_BODY"),
    ("SOFT_BODY", "CLOTH"), ("SOFT_BODY", "SOFT_BODY"),
])
def test_all_v1_role_directions_encode(source_role, target_role):
    payload = wire_entry(_attachment(source_role, target_role),
                         source_vertex_count=3, target_vertex_count=3)
    assert payload["ind"] == [[2, 2, 2, 0, 1, 2]]
    assert payload["w"] == [[1.0, 0.0, 0.0, 0.2, 0.3, 0.5]]
    assert payload["source_points"] == [[1.0, 2.0, 3.0]]
    assert payload["target_points"] == [[4.0, 5.0, 6.0]]
    assert payload["stitch_stiffness"] == 1.0


def test_same_object_stitches_require_explicit_sewing_opt_in():
    from dataclasses import replace

    intra = replace(_attachment("CLOTH", "CLOTH"), target_uuid="source-uuid")
    with pytest.raises(AttachmentError, match="different objects"):
        wire_entry(intra, source_vertex_count=3, target_vertex_count=3)
    payload = wire_entry(replace(intra, allow_same_object=True),
                         source_vertex_count=3, target_vertex_count=3)
    assert payload["source_uuid"] == payload["target_uuid"]
    assert payload["w"] == [[1.0, 0.0, 0.0, 0.2, 0.3, 0.5]]


@pytest.mark.parametrize("role", ["ROD", "RIGID_BODY", "COLLIDER", "STATIC"])
def test_unsupported_roles_are_rejected(role):
    with pytest.raises(AttachmentError):
        wire_entry(_attachment(role, "CLOTH"),
                   source_vertex_count=3, target_vertex_count=3)


def test_invalid_indices_and_weights_are_never_encoded():
    invalid = ObjectAttachment(
        "relation", "Bad", "source", "target", "CLOTH", "CLOTH", 1.0,
        (AttachmentPoint(9, (0, 1, 2), (0.2, 0.2, 0.2),
                         (0, 0, 0), (0, 0, 0)),))
    with pytest.raises(AttachmentError):
        wire_entry(invalid, source_vertex_count=3, target_vertex_count=3)


def test_topology_fingerprint_changes_with_connectivity():
    assert topology_fingerprint(4, ((0, 1, 2), (0, 2, 3))) != (
        topology_fingerprint(4, ((0, 1, 3), (1, 2, 3))))


def test_closest_point_is_barycentric_on_triangle():
    point, weights = closest_point_on_triangle(
        (0.25, 0.25, 2.0), (0, 0, 0), (1, 0, 0), (0, 1, 0))
    assert point == pytest.approx((0.25, 0.25, 0.0))
    assert sum(weights) == pytest.approx(1.0)
    assert weights == pytest.approx((0.5, 0.25, 0.25))


def test_closest_surface_selects_triangle_not_nearest_vertex():
    vertices = ((0, 0, 0), (10, 0, 0), (0, 10, 0),
                (20, 0, 0), (21, 0, 0), (20, 1, 0))
    triangle, weights, point = closest_surface_point(
        (4, 4, 1), vertices, ((3, 4, 5), (0, 1, 2)))
    assert triangle == (0, 1, 2)
    assert point == pytest.approx((4, 4, 0))
    assert sum(weights) == pytest.approx(1.0)


def test_multiple_source_vertices_keep_distinct_barycentric_targets():
    vertices = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
    results = [closest_surface_point(point, vertices, ((0, 1, 2),))
               for point in ((0.1, 0.1, 1), (0.7, 0.1, 1))]
    assert results[0][1] != results[1][1]


def test_every_supported_protocol_adapter_declares_attachment_support():
    assert ADAPTERS
    assert all(adapter.object_attachments for adapter in ADAPTERS.values())


def test_attachment_is_integrated_in_normal_multi_deformable_params():
    attachment = _attachment()
    payload = build_multi_deformable_param_payload(
        SimulationSettings(3, 24.0, (0.0, 0.0, -9.81)),
        (("cloth", "source-uuid", "SHELL", ShellMaterialSettings(), None),
         ("body", "target-uuid", "SOLID", SoftBodyMaterialSettings(), None)),
        (), object_attachments=((attachment, 3, 3),),
        schema_version=2, protocol_version="0.22")
    assert payload["cross_stitch"][0]["source_points"] == [[1.0, 3.0, -2.0]]
    assert payload["cross_stitch"][0]["target_points"] == [[4.0, 6.0, -5.0]]


def test_encoded_blob_contains_exact_cross_object_record():
    attachment = _attachment("SOFT_BODY", "CLOTH")
    blob, _digest = encode_multi_deformable_param(
        SimulationSettings(3, 24.0, (0.0, 0.0, -9.81)),
        (("body", "source-uuid", "SOLID", SoftBodyMaterialSettings(), None),
         ("cloth", "target-uuid", "SHELL", ShellMaterialSettings(), None)),
        (), object_attachments=((attachment, 3, 3),),
        schema_version=2, protocol_version="0.22")
    decoded = envelope.loads_envelope(
        blob, envelope.KIND_PARAM, schema_version=2)
    record = decoded["cross_stitch"][0]
    assert record["source_uuid"] == "source-uuid"
    assert record["target_uuid"] == "target-uuid"
    assert record["ind"] == [[2, 2, 2, 0, 1, 2]]
    assert record["w"] == [[1.0, 0.0, 0.0, 0.2, 0.3, 0.5]]
    assert record["stitch_stiffness"] == 1.0


def test_scene_relationship_survives_rename_and_invalidates_on_delete(
        blender_env):
    env = blender_env
    env.registration.register()
    from cloth_next.blender import object_attachments
    source = env.bpy.types.Object("Source")
    target = env.bpy.types.Object("Target")
    for obj, role, identity in ((source, "CLOTH", "source-id"),
                                (target, "SOFT_BODY", "target-id")):
        obj.cloth_next.enabled = True
        obj.cloth_next.role = role
        obj.cloth_next.persistent_export_id = identity
    scene = env.bpy.types.Scene()
    scene.objects = [source, target]
    vertices = ((0, 0, 0), (1, 0, 0), (0, 1, 0))
    triangles = ((0, 1, 2),)
    entries = tuple(SimpleNamespace(
        obj=obj, role=obj.cloth_next.role, boundary_vertices=vertices,
        boundary_triangles=triangles) for obj in (source, target))
    item = scene.cloth_next_object_attachments.add()
    item.identifier, item.name = "id", "Source to Target"
    item.enabled = True
    item.source_persistent_id, item.target_persistent_id = "source-id", "target-id"
    item.source_role, item.target_role = "CLOTH", "SOFT_BODY"
    item.source_topology = topology_fingerprint(3, triangles)
    item.target_topology = topology_fingerprint(3, triangles)
    point = item.points.add()
    point.source_index = 0
    point.target_triangle = (0, 1, 2)
    point.target_weights = (0.5, 0.25, 0.25)
    point.source_point = (0, 0, 0)
    point.target_point = (0.25, 0.25, 0)

    source.name, target.name = "Renamed Source", "Renamed Target"
    assert len(object_attachments.snapshot_enabled(scene, entries)) == 1
    target.cloth_next.role = "CLOTH"
    entries[1].role = "CLOTH"
    with pytest.raises(AttachmentError, match="role changed"):
        object_attachments.snapshot_enabled(scene, entries)
    target.cloth_next.role = "SOFT_BODY"
    entries[1].role = "SOFT_BODY"
    item.enabled = False
    assert object_attachments.snapshot_enabled(scene, entries) == ()
    item.enabled = True
    scene.objects.remove(target)
    with pytest.raises(AttachmentError, match="Needs Rebuild"):
        object_attachments.snapshot_enabled(scene, entries)
    assert item.needs_rebuild
    scene.cloth_next_object_attachments.remove(0)
    assert len(scene.cloth_next_object_attachments) == 0
    env.registration.unregister()


def test_overlay_handler_lifecycle_is_reload_safe(blender_env):
    env = blender_env
    from cloth_next.blender import object_attachments
    env.registration.register()
    handle = object_attachments._draw_handle
    assert handle is not None
    object_attachments.register()
    assert object_attachments._draw_handle is handle
    env.registration.unregister()
    assert object_attachments._draw_handle is None


def test_vertex_group_attachment_limits_both_ends_and_refreshes(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    from cloth_next.blender import object_attachments

    class Identity:
        def __matmul__(self, point):
            return point

    def mesh_object(name, coordinates, members):
        group = SimpleNamespace(index=0)
        vertices = [SimpleNamespace(
            index=i, co=co, groups=[SimpleNamespace(group=0, weight=1.0)]
            if i in members else []) for i, co in enumerate(coordinates)]
        return SimpleNamespace(
            name=name, type="MESH", mode="OBJECT", matrix_world=Identity(),
            vertex_groups={"Attach": group},
            cloth_next=SimpleNamespace(enabled=True, role="CLOTH",
                                       persistent_export_id=name),
            data=SimpleNamespace(vertices=vertices, calc_loop_triangles=lambda: None,
                                 loop_triangles=[SimpleNamespace(vertices=(0, 1, 2))]))

    source = mesh_object("source", ((.1, 0, 0), (1, 0, 0), (0, 1, 0)), {0})
    target = mesh_object("target", ((0, 0, 0), (2, 0, 0), (0, 2, 0)), {1})
    scene = env.bpy.types.Scene()
    monkeypatch.setattr(env.bpy.context, "scene", scene, raising=False)
    scene.objects = [source, target]
    item = scene.cloth_next_object_attachments.add()
    item.source_persistent_id = "source"
    item.target_object = target
    item.source_group = item.target_group = "Attach"
    item.use_vertex_groups = True
    object_attachments._rebuild_groups(item, scene)
    assert len(item.points) == 1
    assert item.points[0].source_index == 0
    assert item.points[0].target_triangle == (0, 1, 2)
    assert item.points[0].target_weights == (0.0, 1.0, 0.0)
    assert item.points[0].target_point == (2, 0, 0)
    assert not item.needs_rebuild

    # Moving geometry must not silently choose a different target at bake time.
    source.data.vertices[0].co = (100, 100, 100)
    object_attachments._validate_groups(item, scene)
    assert item.points[0].target_weights == (0.0, 1.0, 0.0)

    target.data.vertices[1].groups.clear()
    target.data.vertices[2].groups = [SimpleNamespace(group=0, weight=1)]
    with pytest.raises(AttachmentError, match="changed or are unbound"):
        object_attachments._validate_groups(item, scene)
    assert item.points[0].target_weights == (0.0, 1.0, 0.0)
    object_attachments._rebuild_groups(item, scene)
    assert item.points[0].target_weights == (0.0, 0.0, 1.0)
    target.data.vertices[2].groups[0].weight = 0
    with pytest.raises(AttachmentError, match="no vertices"):
        object_attachments._rebuild_groups(item, scene)
    item.target_group = "Missing"
    with pytest.raises(AttachmentError, match="Choose Vertex Group 2"):
        object_attachments._rebuild_groups(item, scene)
    item.target_object = source
    with pytest.raises(AttachmentError, match="different target"):
        object_attachments._rebuild_groups(item, scene)
    env.registration.unregister()


def test_vertex_search_matches_brute_force_and_breaks_ties():
    import random
    from cloth_next.attachments import VertexSearch
    rng = random.Random(7)
    vertices = [tuple(rng.uniform(-10, 10) for _ in range(3)) for _ in range(1000)]
    indices = tuple(range(0, 1000, 3))
    search = VertexSearch(vertices, indices)
    for _ in range(100):
        point = tuple(rng.uniform(-12, 12) for _ in range(3))
        expected = min(indices, key=lambda i: (
            sum((a - b) ** 2 for a, b in zip(point, vertices[i])), i))
        assert search.nearest(point) == expected
    assert VertexSearch(((1, 0, 0), (-1, 0, 0)), (1, 0)).nearest((0, 0, 0)) == 0
    with pytest.raises(AttachmentError, match="empty"):
        VertexSearch((), ())
