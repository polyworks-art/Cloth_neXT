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
from cloth_next.newton_preview.request_artifact import (
    read_request_artifact, write_request_artifact)
from cloth_next.newton_preview.snapshots import SnapshotStore
from cloth_next.newton_preview.state import (PreviewState, status_label,
                                             transition)
from tests import fake_bpy


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


def test_backend_capabilities_advertise_multi_cloth_and_animated_colliders():
    capabilities = contracts.BackendCapabilities()
    assert capabilities.cloth_objects == 64
    assert capabilities.static_triangle_colliders is True
    assert capabilities.hard_static_pins is True
    assert capabilities.animated_colliders is True
    assert capabilities.deforming_colliders is True
    assert capabilities.follow_animation_pins is True
    assert capabilities.pressure is False


def test_solver_selector_uses_backend_names_without_changing_saved_ids(
        blender_env):
    props = fake_bpy._resolved_props(
        blender_env.object_properties.CLOTHNEXT_PG_solver_backend_settings)
    selector = props["backend"]
    assert selector.keywords["name"] == "Solver"
    assert selector.keywords["default"] == "PPF"
    assert [(item[0], item[1]) for item in selector.keywords["items"]] == [
        ("PPF", "PPF"),
        ("NEWTON", "Newton"),
    ]


def _newton_quality(blender_env, preset="HIGH", **updates):
    from cloth_next.blender import newton_bake
    values = dict(time_step=0.001, min_newton_steps=1,
                  cg_max_iter=10000, cg_tol=0.001)
    values.update(updates)
    scene = SimpleNamespace(
        render=SimpleNamespace(fps=24, fps_base=1.0),
        cloth_next_quality=SimpleNamespace(**values),
        cloth_next_solver=SimpleNamespace(
            quality_preset=preset,
            newton_substeps=updates.get("newton_substeps", 7),
            newton_iterations=updates.get("newton_iterations", 11)))
    cloth = SimpleNamespace(cloth_next=SimpleNamespace(
        collision=SimpleNamespace(enabled=True)))
    return newton_bake._bake_quality(scene, (cloth,))


def test_newton_quality_uses_backend_native_presets_not_ppf_raw_controls(
        blender_env):
    low = _newton_quality(blender_env, "LOW")
    high = _newton_quality(blender_env, "HIGH")
    extreme = _newton_quality(blender_env, "EXTREME")
    changed_ppf = _newton_quality(
        blender_env, "HIGH", time_step=0.0005, min_newton_steps=64,
        cg_max_iter=100000, cg_tol=0.00001)
    custom = _newton_quality(
        blender_env, "CUSTOM", newton_substeps=7, newton_iterations=11)

    assert (low.substeps, low.iterations) == (2, 4)
    assert (high.substeps, high.iterations) == (8, 12)
    assert (extreme.substeps, extreme.iterations) == (16, 20)
    assert (changed_ppf.substeps, changed_ppf.iterations) == (8, 12)
    assert (custom.substeps, custom.iterations) == (7, 11)


def test_newton_quality_request_round_trip_keeps_native_values(blender_env):
    quality = _newton_quality(
        blender_env, "CUSTOM", newton_substeps=7, newton_iterations=11)
    decoded = contracts.PreviewCreateRequest.from_wire(
        _request(quality=quality).to_wire())
    assert decoded.quality == quality


def test_newton_bake_waits_for_regular_bake_window_before_worker_start(
        blender_env, monkeypatch):
    from cloth_next.bake.status import BakeState
    from cloth_next.blender import newton_bake
    shared_controller = newton_bake.shared_controller

    shared_controller.reset()
    request = SimpleNamespace(frame_start=3, frame_end=9)
    session = newton_bake._BakeSession(
        request=request,
        targets=(SimpleNamespace(source_name="Cloth"),),
        cancel_event=__import__("threading").Event(),
        messages=__import__("queue").Queue())
    window_requests = []
    monkeypatch.setattr(newton_bake.newton_preview,
                        "newton_installation_status",
                        lambda: (True, "Ready", Path("python")))
    monkeypatch.setattr(newton_bake, "_capture", lambda _context: session)
    monkeypatch.setattr(
        newton_bake.companion_manager, "begin_bake_mode",
        lambda value: window_requests.append(value) or (True, "started"))

    class Worker:
        def __init__(self, **_kwargs):
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(newton_bake.threading, "Thread", Worker)
    modal_handlers = []
    acquired_locks = []
    monkeypatch.setattr(
        newton_bake.modal_lock, "acquire",
        lambda job_id, *, companion_ready_job_id:
            acquired_locks.append((job_id, companion_ready_job_id)) or True)

    def invoke_modal(_mode, *, job_id):
        operator = newton_bake.CLOTHNEXT_OT_newton_bake_modal()
        operator.job_id = job_id
        manager = SimpleNamespace(
            event_timer_add=lambda *_args, **_kwargs: object(),
            modal_handler_add=lambda value: modal_handlers.append(value))
        return operator.invoke(
            SimpleNamespace(window_manager=manager, window=None), None)

    monkeypatch.setattr(blender_env.bpy.ops.clothnext, "newton_bake_modal",
                        invoke_modal, raising=False)
    context = SimpleNamespace(scene=SimpleNamespace())
    job, waiting = newton_bake.begin(context)

    assert waiting is True
    assert session.worker is None
    assert shared_controller.snapshot().state is BakeState.WAITING_FOR_COMPANION
    assert window_requests[0].job_id == job
    assert (window_requests[0].frame_start,
            window_requests[0].frame_end) == (3, 9)

    monkeypatch.setattr(newton_bake.companion_manager, "startup_status",
                        lambda _job: ("READY", "Bake window ready"))
    monkeypatch.setattr(newton_bake.companion_manager, "consume_ready",
                        lambda _job: True)
    assert newton_bake._startup_pump() is None
    assert session.worker.started is True
    assert shared_controller.snapshot().state is BakeState.EXPORTING
    assert modal_handlers
    assert len(acquired_locks) == 1
    assert acquired_locks[0][0] == acquired_locks[0][1]

    if blender_env.bpy.app.timers.is_registered(newton_bake._pump):
        blender_env.bpy.app.timers.unregister(newton_bake._pump)
    if blender_env.bpy.app.timers.is_registered(newton_bake._startup_pump):
        blender_env.bpy.app.timers.unregister(newton_bake._startup_pump)
    newton_bake._session = None
    shared_controller.fail("test cleanup")
    shared_controller.reset()


def test_newton_bake_publishes_progress_and_imports_cache(blender_env,
                                                          monkeypatch):
    from cloth_next.bake.status import BakeState
    from cloth_next.blender import newton_bake, solver_test
    shared_controller = newton_bake.shared_controller

    shared_controller.reset()
    shared_controller.transition(BakeState.PREPARING, job_id="newton-window")
    shared_controller.transition(BakeState.STARTING_RUN)
    shared_controller.transition(BakeState.EXPORTING)
    request = SimpleNamespace(frame_start=3, frame_end=4)
    target = SimpleNamespace(source_name="Cloth")
    messages = __import__("queue").Queue()
    messages.put(("status", BakeState.STARTING_SOLVER, "Starting Newton"))
    messages.put(("progress", 1, 3, 2))
    header = object()
    messages.put(("finished", (header,)))
    newton_bake._session = newton_bake._BakeSession(
        request=request, targets=(target,),
        cancel_event=__import__("threading").Event(), messages=messages)
    attached = []
    monkeypatch.setattr(newton_bake, "_playback_plan",
                        lambda _session, _target: "plan")
    monkeypatch.setattr(solver_test, "_attach_playback",
                        lambda plan, result: attached.append((plan, result)))

    assert newton_bake._pump() is None
    snapshot = shared_controller.snapshot()
    assert snapshot.state is BakeState.FINISHED
    assert snapshot.progress_current == 2
    assert snapshot.current_frame == 4
    assert attached == [("plan", header)]
    assert newton_bake._session is None
    shared_controller.reset()


def test_request_round_trip_has_stable_scene_identity():
    collider = contracts.PreviewMesh(
        ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0)),
        ((0, 1, 2),))
    second = contracts.PreviewCloth(
        "cloth-2", _request().cloth, (), _request().material)
    animation = contracts.ColliderAnimation(
        0, tuple(collider.vertices for _ in range(10)))
    request = _request(colliders=(collider,), additional_cloths=(second,),
                       collider_animations=(animation,))
    decoded = contracts.PreviewCreateRequest.from_wire(request.to_wire())
    assert decoded == request
    assert decoded.identity() == request.identity()
    assert decoded.total_cloth_vertices == 6


def test_multi_cloth_and_collider_animation_validation_fails_closed():
    mesh = _request().cloth
    material = _request().material
    with pytest.raises(ValueError, match="identifiers"):
        _request(additional_cloths=(
            contracts.PreviewCloth("same", mesh, (), material),
            contracts.PreviewCloth("same", mesh, (), material))).validate()
    collider = contracts.PreviewMesh(mesh.vertices, mesh.triangles)
    with pytest.raises(ValueError, match="sample count"):
        _request(colliders=(collider,), collider_animations=(
            contracts.ColliderAnimation(0, (collider.vertices,)),)).validate()
    changed_topology_sample = collider.vertices[:-1]
    with pytest.raises(ValueError, match="topology"):
        _request(colliders=(collider,), collider_animations=(
            contracts.ColliderAnimation(
                0, tuple(changed_topology_sample for _ in range(10))),)).validate()


def test_animated_pin_round_trip_and_validation():
    request = _request(pin_animations=(contracts.PinAnimation(
        0, tuple((((float(frame), 0.0, 0.0),)) for frame in range(10))),))
    decoded = contracts.PreviewCreateRequest.from_wire(request.to_wire())
    assert decoded == request
    with pytest.raises(ValueError, match="sample count"):
        _request(pin_animations=(contracts.PinAnimation(
            0, (((0.0, 0.0, 0.0),),)),)).validate()


def test_result_vertex_count_includes_every_cloth():
    request = _request(additional_cloths=(contracts.PreviewCloth(
        "second", _request().cloth, (), _request().material),))
    contracts.PreviewResult(
        "session", "scene", 1, 6, "x", "0" * 64).validate_for(request)
    with pytest.raises(ValueError, match="vertex count"):
        contracts.PreviewResult(
            "session", "scene", 1, 3, "x", "0" * 64).validate_for(request)


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


def test_large_scene_request_uses_small_verified_artifact_descriptor(tmp_path):
    wire = _request().to_wire()
    wire["large_diagnostic"] = "x" * (2 * 1024 * 1024)
    metadata = write_request_artifact(tmp_path, wire)
    message = encode_message(command_message(
        "create_preview", request_artifact=metadata,
        result_directory=str(tmp_path)))
    assert len(message) < 4096
    decoded_wire = read_request_artifact(metadata, tmp_path)
    assert decoded_wire["large_diagnostic"] == wire["large_diagnostic"]
    decoded_wire.pop("large_diagnostic")
    assert contracts.PreviewCreateRequest.from_wire(decoded_wire) == _request()


def test_scene_request_artifact_fails_closed_when_replaced_or_outside(tmp_path):
    root = tmp_path / "session"
    metadata = write_request_artifact(root, _request().to_wire())
    Path(metadata["path"]).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="size|checksum"):
        read_request_artifact(metadata, root)
    outside = write_request_artifact(tmp_path / "other", _request().to_wire())
    with pytest.raises(ValueError, match="outside"):
        read_request_artifact(outside, root)


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


def test_newton_registration_defers_orphan_cleanup_under_restrict_data(
        blender_env):
    from cloth_next.blender import newton_preview

    ordinary_data = blender_env.bpy.data

    class _RestrictData:
        pass

    blender_env.bpy.data = _RestrictData()
    try:
        newton_preview.install()
        assert blender_env.bpy.app.timers.is_registered(
            newton_preview._cleanup_orphaned_preview_objects)
    finally:
        blender_env.bpy.data = ordinary_data

    assert newton_preview._cleanup_orphaned_preview_objects() is None
    newton_preview.uninstall()
    assert not blender_env.bpy.app.timers.is_registered(
        newton_preview._cleanup_orphaned_preview_objects)


def test_live_preview_start_defers_scene_capture_to_main_thread_timer(
        blender_env, monkeypatch):
    from cloth_next.blender import newton_preview

    advanced = []

    def steps(_context):
        advanced.append("advanced")
        yield (1, 2, "Collider", 1)

    monkeypatch.setattr(newton_preview, "_capture_steps", steps)
    newton_preview.stop(wait=True)
    newton_preview.start(SimpleNamespace())
    try:
        assert advanced == []
        assert newton_preview._session.state is PreviewState.CAPTURING_SCENE
        assert blender_env.bpy.app.timers.is_registered(newton_preview._poll_timer)
    finally:
        newton_preview.stop(wait=True)


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


def test_animated_collider_accepts_changed_triangulation_with_same_topology(
        blender_env, monkeypatch):
    from cloth_next.blender import newton_preview

    vertices = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    reference = contracts.PreviewMesh(vertices, ((0, 1, 2), (0, 2, 3)))
    flipped = contracts.PreviewMesh(vertices, ((0, 1, 3), (1, 2, 3)))
    samples = iter(((reference, ((0, 1, 2, 3),)),
                    (flipped, ((0, 1, 2, 3),))))
    monkeypatch.setattr(newton_preview, "_evaluated_world_mesh_data",
                        lambda _context, _obj: next(samples))
    scene = SimpleNamespace(frame_set=lambda _frame: None)
    collider = SimpleNamespace(name="Deforming Quad")

    captured = newton_preview._animated_collider_samples(
        SimpleNamespace(), scene, collider, reference, 1, 2)
    assert captured == (vertices, vertices)


def test_animated_collider_rejects_actual_polygon_topology_change(
        blender_env, monkeypatch):
    from cloth_next.blender import newton_preview

    vertices = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                (1.0, 1.0, 0.0), (0.0, 1.0, 0.0))
    reference = contracts.PreviewMesh(vertices, ((0, 1, 2), (0, 2, 3)))
    samples = iter(((reference, ((0, 1, 2, 3),)),
                    (reference, ((0, 1, 2), (0, 2, 3)))))
    monkeypatch.setattr(newton_preview, "_evaluated_world_mesh_data",
                        lambda _context, _obj: next(samples))
    scene = SimpleNamespace(frame_set=lambda _frame: None)
    with pytest.raises(ValueError, match="topology must remain constant"):
        newton_preview._animated_collider_samples(
            SimpleNamespace(), scene, SimpleNamespace(name="Changing"),
            reference, 1, 2)


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


def test_newton_contract_accepts_softbody_only_scene(tmp_path):
    mesh = contracts.PreviewMesh(
        ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)))
    soft = contracts.PreviewSoftBody(
        "soft", mesh, 100.0, 500.0, 0.35, 0.1, 0.5, 0.001, 0.001)
    request = contracts.PreviewCreateRequest(
        "session", "scene", None, (), (), None,
        contracts.PreviewQuality(), 1, 2, 24.0, 1.0,
        (0.0, 0.0, -9.81), str(tmp_path), soft_bodies=(soft,))
    request.validate()
    restored = contracts.PreviewCreateRequest.from_wire(request.to_wire())
    assert restored.soft_bodies == (soft,)
    assert restored.total_cloth_vertices == len(mesh.vertices)


def test_newton_contract_accepts_rigidbody_only_scene(tmp_path):
    mesh = contracts.PreviewMesh(
        ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
        ((0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)))
    rigid = contracts.PreviewRigidBody("rigid", mesh, 100.0, 0.5, 0.001)
    request = contracts.PreviewCreateRequest(
        "session", "scene", None, (), (), None,
        contracts.PreviewQuality(), 1, 2, 24.0, 1.0,
        (0.0, 0.0, -9.81), str(tmp_path), rigid_bodies=(rigid,))
    request.validate()
    assert contracts.PreviewCreateRequest.from_wire(
        request.to_wire()).rigid_bodies == (rigid,)
