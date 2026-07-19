# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Threading, cancellation, and cleanup contracts for the Blender bridge."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from cloth_next.bake import cache_metadata
from cloth_next.bake.status import BakeState
from cloth_next.core.errors import ClothNextError, ErrorCategory, ErrorRecord


def _phase4_meta():
    return {
        "fingerprints": {"settings": "settings", "geometry": "geometry",
                         "combined": "combined", "topology": "topology",
                         "object": "object", "scene": "scene"},
        "identities": {"cloth_next_version": "test",
                       "blender_version": "test", "object": {},
                       "solver": {}},
        "expected": {"vertex_count": 1, "frame_count": 1,
                     "start_frame": 0.0, "sample_rate": 1.0},
        "details": {},
    }


def test_animated_pin_sample_uses_bulk_evaluated_mesh_read(blender_env):
    module = blender_env.solver_test
    depsgraph = object()
    indices = np.asarray((1,), dtype=np.intp)

    class Vertices:
        def __len__(self):
            return 2

        def foreach_get(self, attribute, target):
            assert attribute == "co"
            target[:] = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

    evaluated = SimpleNamespace(
        data=SimpleNamespace(vertices=Vertices()),
        matrix_world=((1.0, 0.0, 0.0, 0.0),
                      (0.0, 1.0, 0.0, 0.0),
                      (0.0, 0.0, 1.0, 0.0),
                      (0.0, 0.0, 0.0, 1.0)))
    obj = SimpleNamespace(
        name="Rigged Cloth",
        evaluated_get=lambda value: evaluated if value is depsgraph else None)
    context = SimpleNamespace(evaluated_depsgraph_get=lambda: (_ for _ in ()).throw(
        AssertionError("a supplied depsgraph must be reused")))
    membership = SimpleNamespace(source_vertex_count=2, vertex_indices=(1,))

    positions = module._sample_evaluated_pin_positions(
        context, obj, membership, depsgraph=depsgraph, index_array=indices)

    assert positions == ((4.0, 6.0, -5.0),)


def test_pin_capture_pump_reuses_frame_depsgraph_without_extra_update(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    depsgraph = object()
    frames = []
    scene = SimpleNamespace(frame_current=1,
                            frame_set=lambda frame: frames.append(frame))
    context = SimpleNamespace(
        scene=scene, evaluated_depsgraph_get=lambda: depsgraph)
    obj = SimpleNamespace(name="Skirt")
    membership = SimpleNamespace(vertex_indices=(2, 4))
    indices = np.asarray((2, 4), dtype=np.intp)
    calls = []

    monkeypatch.setattr(module.bpy.data, "objects", {"Skirt": obj})
    monkeypatch.setattr(module, "_depsgraph_update", lambda _context: (_ for _ in ()).throw(
        AssertionError("frame_set already updates the dependency graph")))
    monkeypatch.setattr(module, "_sample_evaluated_pin_positions",
        lambda passed_context, passed_obj, passed_membership, **kwargs:
            calls.append((passed_context, passed_obj, passed_membership,
                          kwargs["depsgraph"], kwargs["index_array"])) or
            ((1.0, 2.0, 3.0),))
    force_state = module.ForceState((0.0, 0.0, -9.81), (0.0, 0.0, 0.0))
    monkeypatch.setattr(module, "_force_state",
                        lambda _context: (force_state, frozenset()))
    monkeypatch.setattr(module.shared_controller, "update", lambda **_kwargs: None)

    module._pin_capture = {
        "context": context, "targets": (("Skirt", membership),),
        "range": SimpleNamespace(start=1, end=2), "next": 1,
        "samples": {"Skirt": []}, "index_arrays": {"Skirt": indices},
        "force_samples": [], "active_scalar_types": set(),
    }
    try:
        assert module._pin_capture_pump() == 0.005
        assert frames == [1]
        assert calls == [(context, obj, membership, depsgraph, indices)]
        assert module._pin_capture["next"] == 2
        assert module._pin_capture["force_samples"] == [force_state]
    finally:
        module._pin_capture = None


def test_pin_capture_waits_for_companion_before_evaluating_frame(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    calls = []
    monkeypatch.setattr(module.companion_manager, "preparation_status",
                        lambda: ("WAITING", "Opening Bake window…"))
    monkeypatch.setattr(module.shared_controller, "update",
                        lambda **kwargs: calls.append(kwargs))
    scene = SimpleNamespace(frame_set=lambda _frame: (_ for _ in ()).throw(
        AssertionError("frame evaluation started before Companion readiness")))
    module._pin_capture = {
        "context": SimpleNamespace(scene=scene), "targets": (),
        "range": SimpleNamespace(start=1, end=2), "next": 1,
        "samples": {}, "force_samples": [], "active_scalar_types": set(),
        "index_arrays": {}, "wait_for_companion": True,
        "companion_deadline": module.time.monotonic() + 5.0,
    }
    try:
        assert module._pin_capture_pump() == 0.05
        assert calls[-1]["status_message"] == "Opening Bake window…"
    finally:
        module._pin_capture = None


def test_worker_never_accesses_bpy(blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    main_ident = threading.get_ident()
    class Guard:
        def __getattr__(self, name):
            assert threading.get_ident() == main_ident, f"worker touched bpy.{name}"
            return getattr(blender_env.bpy, name)
    module.bpy = Guard()
    class StubSession:
        def __init__(self, **kwargs): self.frame_sink = kwargs["frame_sink"]
        def run(self): return SimpleNamespace()
    monkeypatch.setattr(module, "SolverSession", StubSession)
    monkeypatch.setattr(module.import_result, "build_playback_frames",
                        lambda *args, **kwargs: (((0.0, 0.0, 0.0),),))
    monkeypatch.setattr(module.import_result, "write_playback_cache",
                        lambda *args: SimpleNamespace(vertex_count=1, frame_count=1))
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), ((0.0, 0.0, 0.0),),
                          ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
                          "cloth", tmp_path, tmp_path / "x.pc2", 1)
    thread = threading.Thread(target=module._worker_main, args=(plan,))
    thread.start(); thread.join(2)
    assert not thread.is_alive()
    messages = []
    while not module._queue.empty():
        messages.append(module._queue.get_nowait()[0])
    assert messages[-1] == "finished"


def test_worker_failure_is_printed_persisted_and_sent_to_ui(
        blender_env, monkeypatch, tmp_path, capsys):
    module = blender_env.solver_test

    class FailingSession:
        def __init__(self, **_kwargs): pass
        def run(self): raise RuntimeError("solver exploded at frame 42")

    monkeypatch.setattr(module, "SolverSession", FailingSession)
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0.0, 0.0, 0.0),),
        ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        "cloth", tmp_path / "run", tmp_path / "x.pc2", 1)

    module._worker_main(plan)

    message = module._queue.get_nowait()
    assert message[0] == "error"
    assert "solver exploded at frame 42" in message[2]
    assert str(plan.work_directory / "failure.log") in message[2]
    report = (plan.work_directory / "failure.log").read_text(encoding="utf-8")
    assert "RuntimeError: solver exploded at frame 42" in report
    assert "solver exploded at frame 42" in capsys.readouterr().out


def test_worker_publishes_authenticated_phase4_pair(blender_env, monkeypatch,
                                                    tmp_path):
    module = blender_env.solver_test

    class StubSession:
        def __init__(self, **_kwargs):
            pass

        def run(self):
            return SimpleNamespace(
                timings={}, solver_mode="OWNED_PROCESS",
                package_version="0.1.0", protocol_version="0.11",
                schema_version="1", bytes_transferred=0)

    monkeypatch.setattr(module, "SolverSession", StubSession)
    path = tmp_path / "cn_test_cloth_phase4.pc2"
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0.0, 0.0, 0.0),),
        ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        "cloth", tmp_path / "run", path, 1,
        settings_fingerprint="settings", geometry_fingerprint="geometry",
        material_meta=_phase4_meta())

    module._worker_main(plan)

    messages = []
    while not module._queue.empty():
        messages.append(module._queue.get_nowait())
    assert messages[-1][0] == "finished"
    inspection = cache_metadata.inspect_cache(
        path, settings_fingerprint="settings",
        geometry_fingerprint="geometry")
    assert inspection.condition is cache_metadata.CacheCondition.READY


def test_multi_worker_writes_one_authenticated_cache_per_object(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test

    class StubSession:
        def __init__(self, **kwargs):
            self.sink = kwargs["frame_sink"]

        def run(self):
            positions = {
                "uuid-a": np.asarray(((0.0, 0.0, 1.0),), dtype=np.float32),
                "uuid-b": np.asarray(((2.0, 0.0, 0.0),), dtype=np.float32)}
            self.sink(module.SolverFrame(1, positions["uuid-a"], positions))
            return SimpleNamespace(
                timings={}, solver_mode="OWNED_PROCESS",
                package_version="0.1.0", protocol_version="0.11",
                schema_version="1", bytes_transferred=0)

    monkeypatch.setattr(module, "SolverSession", StubSession)
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    meta_a, meta_b = _phase4_meta(), _phase4_meta()
    meta_a["expected"]["frame_count"] = 2
    meta_b["expected"]["frame_count"] = 2
    targets = (
        module.DeformablePlan(((0.0, 0.0, 0.0),), identity, "A", "uuid-a",
            tmp_path / "a.pc2", "topology-a", meta_a, "CLOTH"),
        module.DeformablePlan(((1.0, 0.0, 0.0),), identity, "B", "uuid-b",
            tmp_path / "b.pc2", "topology-b", meta_b, "CLOTH"))
    scene = SimpleNamespace(cloth_uuid="uuid-a")
    plan = module.RunPlan(
        scene, SimpleNamespace(), targets[0].initial_local, identity, "A",
        tmp_path / "run", targets[0].pc2_path, 2,
        settings_fingerprint="settings", geometry_fingerprint="geometry",
        deformables=targets)

    module._worker_main(plan)

    messages = []
    while not module._queue.empty():
        messages.append(module._queue.get_nowait())
    assert messages[-1][0] == "finished"
    assert set(messages[-1][1]) == {"uuid-a", "uuid-b"}
    for target in targets:
        inspection = cache_metadata.inspect_cache(
            target.pc2_path, settings_fingerprint="settings",
            geometry_fingerprint="geometry")
        assert inspection.condition is cache_metadata.CacheCondition.READY


def test_failed_worker_leaves_unusable_failure_record(blender_env, monkeypatch,
                                                       tmp_path):
    module = blender_env.solver_test

    class FailingSession:
        def __init__(self, **_kwargs):
            pass

        def run(self):
            raise RuntimeError("broken solve")

    monkeypatch.setattr(module, "SolverSession", FailingSession)
    path = tmp_path / "cn_test_cloth_failed.pc2"
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0.0, 0.0, 0.0),),
        ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        "cloth", tmp_path / "run", path, 1,
        settings_fingerprint="settings", geometry_fingerprint="geometry",
        material_meta=_phase4_meta())

    module._worker_main(plan)

    assert module._queue.get_nowait()[0] == "error"
    inspection = cache_metadata.inspect_cache(path)
    assert inspection.condition is cache_metadata.CacheCondition.PARTIAL
    assert not path.exists()


def test_convergence_failure_names_blender_frame_and_action(blender_env):
    module = blender_env.solver_test
    plan = SimpleNamespace(frame_start=1)
    error = ClothNextError(ErrorRecord.create(
        category=ErrorCategory.SIMULATION,
        user_message="The solver rejected the status request.",
        technical_message=("server error during status: Linear solver failed "
                           "to converge: advance failed at frame 41"),
        recommended_action="Retry."))

    summary, details = module._present_worker_error(plan, error)

    assert summary == "Simulation could not converge at Blender frame 42."
    assert "Stage: collision and constraint solve" in details
    assert "What to do:" in details


def test_force_empties_replace_scene_gravity_and_add_wind(blender_env):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))

    def force(name, force_type, strength):
        return SimpleNamespace(
            name=name, name_full=name, type="EMPTY", matrix_world=identity,
            cloth_next=SimpleNamespace(enabled=True, role="FORCE",
                force=SimpleNamespace(force_type=force_type,
                                      strength=strength)))

    context = SimpleNamespace(scene=SimpleNamespace(
        objects=(force("Gravity", "GRAVITY", 4.0),
                 force("Wind", "WIND", 2.5)),
        gravity=(0.0, 0.0, -9.81), use_gravity=True))
    gravity, wind = module._force_vectors(context)
    assert gravity == (0.0, 0.0, -4.0)
    assert wind == (0.0, 0.0, 2.5)


def test_scalar_ppf_force_empties_are_aggregated(blender_env):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))

    def force(name, force_type, **values):
        settings = dict(strength=1.0, air_density=0.001,
                        air_friction=0.2, vertex_air_damp=0.0)
        settings.update(values)
        return SimpleNamespace(
            name=name, name_full=name, type="EMPTY", matrix_world=identity,
            cloth_next=SimpleNamespace(enabled=True, role="FORCE",
                force=SimpleNamespace(force_type=force_type, **settings)))

    context = SimpleNamespace(scene=SimpleNamespace(
        objects=(force("Density A", "AIR_DENSITY", air_density=0.8),
                 force("Density B", "AIR_DENSITY", air_density=0.4),
                 force("Friction", "AIR_FRICTION", air_friction=0.3),
                 force("Drag", "VERTEX_AIR_DAMP", vertex_air_damp=0.15)),
        gravity=(0.0, 0.0, -9.81), use_gravity=True))
    state, active = module._force_state(context)
    assert state.air_density == pytest.approx(1.2)
    assert state.air_friction == pytest.approx(0.3)
    assert state.vertex_air_damp == pytest.approx(0.15)
    assert active == {"AIR_DENSITY", "AIR_FRICTION", "VERTEX_AIR_DAMP"}


def test_native_force_animation_is_sampled_for_ppf_dyn_params(blender_env):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    force_settings = SimpleNamespace(
        force_type="WIND", strength=1.0, air_density=0.001,
        air_friction=0.2, vertex_air_damp=0.0)
    force = SimpleNamespace(
        name="Animated Wind", name_full="Animated Wind", type="EMPTY",
        matrix_world=identity,
        cloth_next=SimpleNamespace(enabled=True, role="FORCE",
                                   force=force_settings))

    class Scene:
        objects = (force,)
        gravity = (0.0, 0.0, -9.81)
        use_gravity = True
        frame_current = 8
        render = SimpleNamespace(fps=20)

        def frame_set(self, frame, **_kwargs):
            self.frame_current = frame
            force_settings.strength = float(frame)

    context = SimpleNamespace(scene=Scene(), view_layer=None)
    capture = module._capture_force_animation(
        context, module.BakeFrameRange(1, 3))
    assert context.scene.frame_current == 8
    assert capture.initial.wind == (0.0, 0.0, 1.0)
    assert capture.dynamic_parameters == (("wind", (
        (0.0, (0.0, 0.0, 1.0), False),
        (0.05, (0.0, 0.0, 2.0), False),
        (0.1, (0.0, 0.0, 3.0), False))),)

def test_companion_cancelling_snapshot_sets_worker_event(blender_env):
    module = blender_env.solver_test
    module._cancel_event.clear()
    module._worker = SimpleNamespace(is_alive=lambda: True)
    module._on_controller_snapshot(SimpleNamespace(state=BakeState.CANCELLING))
    assert module._cancel_event.is_set()
    module._worker = None

def test_unregister_clears_solver_worker_timer_and_subscription(blender_env):
    module = blender_env.solver_test
    blender_env.registration.register()
    module._unsubscribe = lambda: None
    blender_env.bpy.app.timers.register(module._pump)
    blender_env.registration.unregister()
    assert module._worker is None
    assert module._unsubscribe is None
    assert not blender_env.bpy.app.timers.is_registered(module._pump)


def test_attach_reuses_owned_modifier(blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    old = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    old.filepath = str(tmp_path / "cn_test_cloth_old.pc2")
    module.mark_owned_playback(obj, old, old.filepath)
    path = tmp_path / "cn_test_cloth_new.pc2"
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
                          ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
                          obj.name, tmp_path, path, 1)

    module._attach_playback(plan, header)

    assert len(obj.modifiers) == 1
    assert obj.modifiers[0] is old
    assert old.filepath == str(path)


def test_animated_collider_samples_are_dense_and_include_exact_endpoints(
        blender_env):
    module = blender_env.solver_test
    points = module._collider_sample_points(
        module.BakeFrameRange(10, 11), 24)
    assert len(points) == 9
    assert points[0] == (10, 0.0, 0.0)
    assert points[-1] == (11, 0.0, 1.0 / 24.0)
    assert points[1] == (10, 0.125, 1.0 / 192.0)
    with pytest.raises(ValueError):
        module._collider_sample_points(module.BakeFrameRange(1, 2), 24, 1)


def test_animated_collider_topology_ignores_quad_diagonal_flip(blender_env):
    """Armature deformation may retessellate a quad without changing it."""
    module = blender_env.solver_test
    polygons = ((0, 1, 2, 3), (3, 2, 4))
    assert module._collider_topology_change(
        5, polygons, 5, polygons) == ""


def test_animated_collider_topology_detects_real_changes(blender_env):
    module = blender_env.solver_test
    polygons = ((0, 1, 2, 3),)
    assert "vertex count changed" in module._collider_topology_change(
        4, polygons, 5, polygons)
    assert "polygon topology changed" in module._collider_topology_change(
        4, polygons, 4, ((0, 1, 2), (0, 2, 3)))


def test_animated_collider_bulk_topology_reuses_buffers(blender_env):
    module = blender_env.solver_test

    class BulkCollection:
        def __init__(self, **columns):
            self.columns = columns

        def __len__(self):
            return len(next(iter(self.columns.values())))

        def foreach_get(self, name, target):
            target[:] = self.columns[name]

    mesh = SimpleNamespace(
        polygons=BulkCollection(loop_start=[0, 4], loop_total=[4, 3]),
        loops=BulkCollection(vertex_index=[0, 1, 2, 3, 3, 2, 4]))
    first = module._collider_topology_arrays(mesh)
    second = module._collider_topology_arrays(mesh, first)

    assert all(left is right for left, right in zip(first, second))
    assert module._collider_array_topology_change(5, first, 5, second) == ""
    mesh.loops.columns["vertex_index"][-1] = 1
    changed = module._collider_topology_arrays(mesh)
    assert "polygon topology changed" in module._collider_array_topology_change(
        5, first, 5, changed)


def test_dense_animated_collider_capture_returns_non_blocking_warning(
        blender_env):
    module = blender_env.solver_test
    vertices = range(214_050)
    collider = SimpleNamespace(name="Character Proxy",
        data=SimpleNamespace(vertices=vertices),
        cloth_next=SimpleNamespace(collider_motion="ANIMATED",
                                   collider_samples_per_frame=8))
    warning = module.animated_collider_capture_warning(
        (collider,), module.BakeFrameRange(1, 150))

    assert warning is not None
    assert warning.collider_name == "Character Proxy"
    assert warning.vertex_count == 214_050
    assert warning.samples_per_frame == 8
    assert warning.size_label == "2.85 GiB"


def test_reasonable_animated_collider_capture_stays_allowed(blender_env):
    module = blender_env.solver_test
    vertices = range(10_000)
    collider = SimpleNamespace(name="Character Proxy",
        data=SimpleNamespace(vertices=vertices),
        cloth_next=SimpleNamespace(collider_motion="ANIMATED",
                                   collider_samples_per_frame=8))
    warning = module.animated_collider_capture_warning(
        (collider,), module.BakeFrameRange(1, 150))

    assert warning is None


def test_multi_attach_rolls_back_first_modifier_if_second_attach_fails(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    objects = []
    targets = []
    headers = {}
    for index in range(2):
        obj = blender_env.bpy.types.Object(name=f"cloth-{index}", type="MESH")
        blender_env.bpy.data.objects[obj.name] = obj
        objects.append(obj)
        path = tmp_path / f"cn_test_cloth_new_{index}.pc2"
        target = module.DeformablePlan(
            ((0, 0, 0),), identity, obj.name, f"uuid-{index}", path,
            "topology", {"details": {}}, "CLOTH")
        targets.append(target)
        headers[target.uuid] = SimpleNamespace(vertex_count=1, frame_count=1)
    old_path = tmp_path / "cn_test_cloth_old.pc2"
    old_path.write_bytes(b"old")
    old = objects[0].modifiers.new(
        module.import_result.MODIFIER_NAME, "MESH_CACHE")
    old.filepath = str(old_path)
    module.mark_owned_playback(objects[0], old, old.filepath)
    monkeypatch.setattr(module.pc2, "read_header",
                        lambda path: headers[next(
                            target.uuid for target in targets
                            if target.pc2_path == path)])
    monkeypatch.setattr(module.cache_metadata, "inspect_cache",
                        lambda *_args, **_kwargs: SimpleNamespace(
                            usable=True, condition=SimpleNamespace(value="VALID"),
                            message="", metadata={}))
    monkeypatch.setattr(objects[1].modifiers, "new",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("second modifier failed")))
    first = targets[0]
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), first.initial_local, identity,
        first.object_name, tmp_path, first.pc2_path, 1,
        settings_fingerprint="settings", geometry_fingerprint="geometry",
        material_meta=first.material_meta, deformables=tuple(targets))

    with pytest.raises(RuntimeError, match="second modifier failed"):
        module._attach_playback(plan, headers)

    assert old.filepath == str(old_path)
    assert old_path.exists()
    assert len(objects[1].modifiers) == 0


def test_attach_collapses_all_marked_modifiers_after_repeated_bakes(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    first = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    first.filepath = str(tmp_path / "cn_test_cloth_first.pc2")
    module.mark_owned_playback(obj, first, first.filepath)
    second = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    second.filepath = str(tmp_path / "cn_test_cloth_second.pc2")
    module.mark_owned_playback(obj, second, second.filepath)
    # mark_owned_playback stores only the second path on the object, so the
    # old strict ownership predicate intentionally no longer matches first.
    assert not module.is_cloth_next_playback_modifier(obj, first)
    path = tmp_path / "cn_test_cloth_third.pc2"
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
                          ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
                          obj.name, tmp_path, path, 1)

    module._attach_playback(plan, header)

    assert list(obj.modifiers) == [first]
    assert first.filepath == str(path)


def test_attach_succeeds_when_post_import_housekeeping_fails(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    path = tmp_path / "cn_test_cloth_new.pc2"
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    monkeypatch.setattr(
        module, "mark_owned_playback",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("metadata boom")))
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
        ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
        obj.name, tmp_path, path, 1)

    module._attach_playback(plan, header)

    assert len(obj.modifiers) == 1
    assert obj.modifiers[0].filepath == str(path)


def test_pump_exception_becomes_terminal_error(blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    module.shared_controller.transition(BakeState.PREPARING)
    module._active_plan = SimpleNamespace()
    module._worker = SimpleNamespace(is_alive=lambda: False)
    monkeypatch.setattr(module, "_pump_once",
                        lambda: (_ for _ in ()).throw(TypeError("attach boom")))

    assert module._pump() is None
    assert module._active_plan is None
    assert module._worker is None
    snapshot = module.shared_controller.snapshot()
    assert snapshot.state is BakeState.ERROR
    assert "attach boom" in snapshot.error_details


def test_ram_safety_cancel_becomes_actionable_error(blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    module.shared_controller.transition(BakeState.PREPARING)
    module.shared_controller.transition(BakeState.EXPORTING)
    module.shared_controller.request_cancel()
    module._active_plan = SimpleNamespace()
    module._worker = SimpleNamespace(is_alive=lambda: True)
    module._ram_auto_cancel_enabled = False
    module._ram_auto_cancel_triggered = True
    monkeypatch.setattr(module, "_discard_incomplete", lambda *_a, **_k: None)
    while not module._queue.empty():
        module._queue.get_nowait()
    module._queue.put(("cancelled", None, None))

    assert module._pump_once() is None
    snapshot = module.shared_controller.snapshot()
    assert snapshot.state is BakeState.ERROR
    assert snapshot.error_code == "CNX-E166"
    assert module._ram_auto_cancel_triggered is False

def test_run_operator_reports_optional_companion_warning(blender_env, monkeypatch):
    module=blender_env.solver_test
    monkeypatch.setattr(module,"start_run",lambda _context, **_kw:"bundle unavailable")
    op=module.CLOTHNEXT_OT_solver_test_run()
    assert op.execute(blender_env.bpy.context)=={"FINISHED"}
    assert op.reports[-1][0]=={"WARNING"}
    assert "bundle unavailable" in op.reports[-1][1]

def test_companion_ensure_running_reuses_existing(blender_env, monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    monkeypatch.setattr(manager,"running",lambda:True)
    monkeypatch.setattr(manager,"launch",lambda: (_ for _ in ()).throw(AssertionError("duplicate")))
    assert manager.ensure_running()==(True,"Bake window reused")


def test_companion_preparation_ready_requires_tk_ready_message(blender_env,
                                                                 monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    manager._transport_ready=False
    manager._process=SimpleNamespace(poll=lambda:None)
    assert manager.preparation_status()[0]=="WAITING"
    manager._transport_ready=True
    assert manager.preparation_status()[0]=="READY"
    manager._transport_ready=False
    manager._process=None

def test_companion_replaces_exited_session_without_leaking(blender_env,
                                                            monkeypatch):
    manager=__import__("cloth_next.blender.companion_manager",fromlist=["x"])
    manager._process=SimpleNamespace(poll=lambda:1)
    manager._server=SimpleNamespace()
    manager._unsubscribe=lambda:None
    calls=[]
    monkeypatch.setattr(manager,"shutdown",lambda:calls.append("shutdown"))
    monkeypatch.setattr(manager,"launch",lambda:calls.append("launch") or
                        (True,"Bake window launched"))
    assert manager.ensure_running()==(True,"Bake window launched")
    assert calls==["shutdown","launch"]
