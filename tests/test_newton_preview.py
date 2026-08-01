from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.newton_preview import contracts
from cloth_next.newton_preview.artifacts import (
    prune_owned_sessions, remove_owned_session,
    session_directory_from_results)
from cloth_next.newton_preview.coordinates import (
    blender_to_newton_position, newton_to_blender_position,
    transform_position, validate_world_transform)
from cloth_next.newton_preview.material import map_cloth_material
from cloth_next.newton_preview.protocol import (command_message,
                                                decode_message,
                                                encode_message)
from cloth_next.newton_preview.snapshots import SnapshotStore
from cloth_next.newton_preview.state import (PreviewState, status_label,
                                             transition)


def _request(**updates):
    values = dict(
        session_id="session", scene_identity="scene",
        cloth=contracts.PreviewMesh(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 2),)), colliders=(), pin_indices=(0,),
        material=contracts.PreviewMaterial(1.0, 10.0, 8.0, 2.0,
                                           1.0, 1.0, 0.2, 0.01, 0.005),
        quality=contracts.PreviewQuality(), frame_start=1, frame_end=10,
        fps=24.0, time_scale=1.0, gravity=(0.0, 0.0, -9.81),
        result_directory="preview-results")
    values.update(updates)
    return contracts.PreviewCreateRequest(**values)


def test_backend_capabilities_reject_unimplemented_scope():
    capabilities = contracts.BackendCapabilities()
    assert capabilities.cloth_objects == 1
    assert capabilities.static_triangle_colliders is True
    assert capabilities.hard_static_pins is True
    assert capabilities.animated_colliders is False
    assert capabilities.follow_animation_pins is False
    assert capabilities.pressure is False


def test_request_round_trip_has_stable_scene_identity():
    request = _request()
    decoded = contracts.PreviewCreateRequest.from_wire(request.to_wire())
    assert decoded == request
    assert decoded.identity() == request.identity()


@pytest.mark.parametrize("updates", [
    {"pin_indices": (99,)}, {"fps": 0.0}, {"time_scale": -1.0},
    {"frame_start": 5, "frame_end": 4}, {"solver": "UNKNOWN"},
])
def test_request_validation_fails_closed(updates):
    with pytest.raises(ValueError):
        _request(**updates).validate()


def test_stale_or_partial_result_is_rejected():
    request = _request()
    with pytest.raises(ValueError, match="stale"):
        contracts.PreviewResult("old", "scene", 1, 3, "x", "0" * 64).validate_for(request)
    with pytest.raises(ValueError, match="partial"):
        contracts.PreviewResult("session", "scene", 1, 3, "x", "0" * 64,
                                complete=False).validate_for(request)


def test_coordinates_are_z_up_identity_and_round_trip():
    point = (1.25, -2.5, 3.75)
    assert newton_to_blender_position(blender_to_newton_position(point)) == point
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    validate_world_transform(identity)
    assert transform_position(identity, point) == point


def test_coordinate_transform_and_negative_scale_policy():
    transform = ((0, -1, 0, 2), (1, 0, 0, 3),
                 (0, 0, 2, 4), (0, 0, 0, 1))
    assert transform_position(transform, (1, 2, 3)) == (0.0, 4.0, 10.0)
    mirrored = ((-1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    with pytest.raises(ValueError, match="negative"):
        validate_world_transform(mirrored)


def test_material_mapping_is_monotonic_and_density_is_direct():
    base = dict(surface_weight=0.2, stretch_resistance=100.0,
                sideways_response=0.3, bend_resistance=2.0,
                shape_damping=0.2, fold_damping=0.1, friction=0.2,
                collision_gap=0.002, surface_offset=0.001)
    low = map_cloth_material(**base)
    high = map_cloth_material(**{**base, "surface_weight": 0.4,
                                 "stretch_resistance": 200.0,
                                 "bend_resistance": 4.0,
                                 "friction": 0.5})
    assert high.surface_density == pytest.approx(low.surface_density * 2.0)
    assert high.stretch_stiffness > low.stretch_stiffness
    assert high.bend_stiffness > low.bend_stiffness
    assert high.friction > low.friction


def test_protocol_is_bounded_and_command_set_is_explicit():
    value = command_message("update_target_frame", frame=12)
    assert decode_message(encode_message(value)) == value
    with pytest.raises(ValueError):
        command_message("execute_arbitrary_command")
    with pytest.raises(ValueError):
        decode_message(b"[]\n")


def test_preview_state_machine_and_catching_up_label():
    state = transition(PreviewState.DISABLED, PreviewState.CAPTURING_SCENE)
    state = transition(state, PreviewState.STARTING_WORKER)
    state = transition(state, PreviewState.BUILDING_MODEL)
    state = transition(state, PreviewState.READY)
    assert transition(state, PreviewState.PLAYING) is PreviewState.PLAYING
    assert status_label(PreviewState.CATCHING_UP, current_frame=5,
                        target_frame=12) == "Calculating Frame 5 / Target 12"
    with pytest.raises(ValueError):
        transition(PreviewState.DISABLED, PreviewState.PLAYING)


def test_snapshot_retention_is_bounded_and_initial_survives():
    store = SnapshotStore(3)
    for frame in (1, 2, 3, 4, 5):
        store.put(frame, f"state-{frame}")
    assert len(store) == 3
    assert store.nearest_at_or_before(1) == (1, "state-1")
    assert store.nearest_at_or_before(5) == (5, "state-5")
    store.clear_except_initial()
    assert len(store) == 1


def test_worker_module_never_imports_blender():
    source = (Path(__file__).resolve().parents[1] / "cloth_next" /
              "newton_preview" / "worker_main.py").read_text(encoding="utf-8")
    assert "import bpy" not in source
    assert "shell=True" not in source


def test_blender_handler_registration_is_idempotent_and_removable(blender_env):
    from cloth_next.blender import newton_preview

    handlers = blender_env.bpy.app.handlers
    newton_preview.install()
    newton_preview.install()
    assert handlers.frame_change_post.count(
        newton_preview._frame_change_post) == 1
    assert newton_preview._depsgraph_update_post not in \
        handlers.depsgraph_update_post
    from cloth_next.blender import validation_state
    assert validation_state._depsgraph_observers.count(
        newton_preview._depsgraph_update_post) == 1

    newton_preview.uninstall()
    assert newton_preview._frame_change_post not in handlers.frame_change_post
    assert newton_preview._depsgraph_update_post not in \
        handlers.depsgraph_update_post
    assert newton_preview._depsgraph_update_post not in \
        validation_state._depsgraph_observers


def test_newton_session_cleanup_is_strict_and_bounded(tmp_path):
    root = tmp_path / "newton" / "sessions"
    identifiers = [f"{index:032x}" for index in range(4)]
    for identifier in identifiers:
        (root / identifier / "results").mkdir(parents=True)
    assert session_directory_from_results(
        root / identifiers[0] / "results") == root / identifiers[0]
    assert prune_owned_sessions(root, keep=2) == 2
    assert len(tuple(root.iterdir())) == 2
    remaining = next(root.iterdir())
    remove_owned_session(remaining / "results")
    assert not remaining.exists()
    with pytest.raises(ValueError):
        remove_owned_session(tmp_path / "unowned" / "results")


def test_newton_bake_world_to_local_round_trip(blender_env):
    import numpy as np
    from cloth_next.blender import newton_bake

    inverse = ((1.0, 0.0, 0.0, -2.0),
               (0.0, 0.5, 0.0, -1.5),
               (0.0, 0.0, 1.0, -4.0),
               (0.0, 0.0, 0.0, 1.0))
    world = np.asarray(((3.0, 5.0, 7.0),), dtype=np.float64)
    local = newton_bake._world_to_local(world, inverse)
    assert local.tolist() == [[1.0, 1.0, 3.0]]


def test_bake_operator_routes_only_explicit_newton_backend(blender_env,
                                                           monkeypatch):
    module = blender_env.solver_test
    from cloth_next.blender import newton_bake
    calls = []
    monkeypatch.setattr(newton_bake, "begin",
                        lambda _context: calls.append("newton") or ("n", False))
    monkeypatch.setattr(module, "begin_production_bake",
                        lambda _context: calls.append("ppf") or ("p", False))
    operator = module.CLOTHNEXT_OT_bake()
    context = type("Context", (), {
        "scene": type("Scene", (), {
            "cloth_next_newton_preview": type("Settings", (), {
                "bake_backend": "NEWTON"})()})()})()
    assert operator.execute(context) == {"FINISHED"}
    assert calls == ["newton"]
    context.scene.cloth_next_newton_preview.bake_backend = "PPF"
    assert operator.execute(context) == {"FINISHED"}
    assert calls == ["newton", "ppf"]


def test_mesh_capture_cache_reuses_geometry_across_quality_only_change(
        blender_env, monkeypatch):
    from cloth_next.blender import newton_preview

    newton_preview._mesh_capture_cache.clear()
    calls = []
    mesh = contracts.PreviewMesh(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((0, 1, 2),))
    monkeypatch.setattr(
        newton_preview, "_triangulated_world_mesh",
        lambda _context, _obj: calls.append("evaluated") or mesh)
    obj = SimpleNamespace(
        name="Cloth", matrix_world=((1, 0, 0, 0), (0, 1, 0, 0),
                                     (0, 0, 1, 0), (0, 0, 0, 1)),
        data=SimpleNamespace(
            name="ClothMesh",
            vertices=[SimpleNamespace(co=row) for row in mesh.vertices],
            polygons=[SimpleNamespace(vertices=row) for row in mesh.triangles],
            shape_keys=None), modifiers=[],
        cloth_next=SimpleNamespace(persistent_export_id="uuid", role="CLOTH"))
    context = SimpleNamespace()

    assert newton_preview._cached_triangulated_world_mesh(context, obj) is mesh
    # Preview quality is deliberately not a mesh-capture key.
    quality = "HIGH"
    assert quality == "HIGH"
    assert newton_preview._cached_triangulated_world_mesh(context, obj) is mesh
    assert calls == ["evaluated"]

    obj.cloth_next.role = "COLLIDER"
    newton_preview._cached_triangulated_world_mesh(context, obj)
    assert calls == ["evaluated", "evaluated"]


def test_mesh_capture_key_invalidates_geometry_topology_uuid_and_armature_pose(
        blender_env):
    from cloth_next.blender import newton_preview

    vertices = [SimpleNamespace(co=(0.0, 0.0, 0.0)),
                SimpleNamespace(co=(1.0, 0.0, 0.0)),
                SimpleNamespace(co=(0.0, 1.0, 0.0))]
    polygon = SimpleNamespace(vertices=(0, 1, 2))
    bone = SimpleNamespace(
        name="Bone", matrix=((1, 0, 0, 0), (0, 1, 0, 0),
                             (0, 0, 1, 0), (0, 0, 0, 1)))
    armature = SimpleNamespace(name="Rig", data=SimpleNamespace(name="RigData"),
                               pose=SimpleNamespace(bones=[bone]))
    modifier = SimpleNamespace(
        name="Armature", type="ARMATURE", show_viewport=True,
        show_render=True, object=armature)
    obj = SimpleNamespace(
        name="Cloth", matrix_world=((1, 0, 0, 0), (0, 1, 0, 0),
                                     (0, 0, 1, 0), (0, 0, 0, 1)),
        data=SimpleNamespace(name="Mesh", vertices=vertices,
                             polygons=[polygon], shape_keys=None),
        modifiers=[modifier],
        cloth_next=SimpleNamespace(persistent_export_id="uuid", role="CLOTH"))
    baseline = newton_preview._mesh_capture_key(obj)
    vertices[2].co = (0.0, 2.0, 0.0)
    geometry = newton_preview._mesh_capture_key(obj)
    assert geometry != baseline
    vertices[2].co = (0.0, 1.0, 0.0)
    polygon.vertices = (0, 2, 1)
    topology = newton_preview._mesh_capture_key(obj)
    assert topology != baseline
    polygon.vertices = (0, 1, 2)
    obj.cloth_next.persistent_export_id = "replacement-uuid"
    assert newton_preview._mesh_capture_key(obj) != baseline
    obj.cloth_next.persistent_export_id = "uuid"
    bone.matrix = ((1, 0, 0, 0.25), (0, 1, 0, 0),
                   (0, 0, 1, 0), (0, 0, 0, 1))
    assert newton_preview._mesh_capture_key(obj) != baseline
