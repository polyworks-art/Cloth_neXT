# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Threading, cancellation, and cleanup contracts for the Blender bridge."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from cloth_next.bake import cache_metadata, pc2
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


def test_scene_fps_uses_blender_fps_base(blender_env):
    module = blender_env.solver_test
    context = SimpleNamespace(scene=SimpleNamespace(
        render=SimpleNamespace(fps=30, fps_base=1.001)))

    assert module._scene_fps(context) == pytest.approx(30.0 / 1.001)


def test_animated_collider_motion_digest_covers_all_axes_and_times(blender_env):
    module = blender_env.solver_test
    offsets = (0.0, 1.0)
    base = np.zeros((2, 3, 3), dtype=np.float32)
    baseline = module._collider_motion_digest(offsets, base, dtype="<f4")
    assert baseline == module._collider_motion_digest(offsets, base.copy(), dtype="<f4")

    for axis in range(3):
        moved = base.copy()
        moved[1, :, axis] = float(axis + 1)
        assert module._collider_motion_digest(offsets, moved, dtype="<f4") != baseline

    assert module._collider_motion_digest((0.0, 2.0), base, dtype="<f4") != baseline
    assert module.SCENE_EXPORT_CACHE_SCHEMA == 4


def test_shell_uv_export_preserves_authored_uvs_and_generates_fallback(
        blender_env):
    module = blender_env.solver_test
    triangle = SimpleNamespace(loops=(0, 1, 2))
    mesh = SimpleNamespace(
        loop_triangles=(triangle,), calc_loop_triangles=lambda: None,
        uv_layers=SimpleNamespace(active=SimpleNamespace(data=(
            SimpleNamespace(uv=(0.2, 0.3)),
            SimpleNamespace(uv=(0.8, 0.3)),
            SimpleNamespace(uv=(0.2, 0.9))))))
    obj = SimpleNamespace(name="Cloth", data=mesh)

    assert module._extract_source_uv_faces(obj) == (
        ((0.2, 0.3), (0.8, 0.3), (0.2, 0.9)),)

    mesh.uv_layers.active = None
    assert module._extract_source_uv_faces(obj) == (
        ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),)


def test_vertex_group_friction_blends_weights_and_averages_faces(blender_env):
    module = blender_env.solver_test
    region = SimpleNamespace(vertex_group="Rough", friction=0.8)
    vertices = (
        SimpleNamespace(groups=()),
        SimpleNamespace(groups=(SimpleNamespace(group=3, weight=0.5),)),
        SimpleNamespace(groups=(SimpleNamespace(group=3, weight=1.0),)),
    )
    group = SimpleNamespace(index=3)
    obj = SimpleNamespace(
        name="Cloth", data=SimpleNamespace(vertices=vertices),
        vertex_groups=SimpleNamespace(get=lambda name: group if name == "Rough" else None),
        cloth_next=SimpleNamespace(friction_regions=(region,)))

    result = module._extract_face_friction(obj, ((0, 1, 2),), 0.2)

    # Vertex values are 0.2, 0.5, 0.8; the triangle receives their mean.
    assert result == pytest.approx((0.5,))


def test_overlapping_friction_groups_mix_their_targets(blender_env):
    module = blender_env.solver_test
    regions = (SimpleNamespace(vertex_group="A", friction=0.0),
               SimpleNamespace(vertex_group="B", friction=1.0))
    memberships = (SimpleNamespace(group=1, weight=1.0),
                   SimpleNamespace(group=2, weight=1.0))
    vertex = SimpleNamespace(groups=memberships)
    groups = {"A": SimpleNamespace(index=1), "B": SimpleNamespace(index=2)}
    obj = SimpleNamespace(
        name="Cloth", data=SimpleNamespace(vertices=(vertex, vertex, vertex)),
        vertex_groups=SimpleNamespace(get=groups.get),
        cloth_next=SimpleNamespace(friction_regions=regions))

    assert module._extract_face_friction(
        obj, ((0, 1, 2),), 0.2) == pytest.approx((0.5,))


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
        modifiers=(SimpleNamespace(type="ARMATURE", show_viewport=True),),
        evaluated_get=lambda value: evaluated if value is depsgraph else None)
    context = SimpleNamespace(evaluated_depsgraph_get=lambda: (_ for _ in ()).throw(
        AssertionError("a supplied depsgraph must be reused")))
    membership = SimpleNamespace(source_vertex_count=2, vertex_indices=(1,))

    positions = module._sample_evaluated_pin_positions(
        context, obj, membership, depsgraph=depsgraph, index_array=indices)

    assert positions == ((4.0, 6.0, -5.0),)


def test_pin_capture_disables_modifiers_after_last_armature(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Skirt", type="MESH")
    before = obj.modifiers.new("Before Rig", "SMOOTH")
    rig = obj.modifiers.new("Armature", "ARMATURE")
    after = obj.modifiers.new("Mirror Display", "MIRROR")
    playback = obj.modifiers.new("Cloth NeXt Playback", "MESH_CACHE")
    before.show_viewport = rig.show_viewport = True
    after.show_viewport = playback.show_viewport = True
    before.show_render = rig.show_render = True
    after.show_render = playback.show_render = True
    monkeypatch.setattr(module.bpy.data, "objects", {"Skirt": obj})
    monkeypatch.setattr(
        module, "is_cloth_next_playback_modifier",
        lambda _obj, modifier: modifier is playback)
    updates = []
    monkeypatch.setattr(module, "_depsgraph_update",
                        lambda _context: updates.append(True))
    state = {
        "context": SimpleNamespace(),
        "targets": (("Skirt", SimpleNamespace()),),
    }

    module._suspend_pin_capture_playback(state)

    assert before.show_viewport
    assert rig.show_viewport
    assert not after.show_viewport
    assert not playback.show_viewport
    module._restore_pin_capture_state({
        **state,
        "original": 1,
        "original_subframe": 0.0,
        "context": SimpleNamespace(scene=SimpleNamespace(
            frame_set=lambda *_args, **_kwargs: None)),
    })
    assert after.show_viewport and after.show_render
    assert playback.show_viewport and playback.show_render
    assert len(updates) == 1


def test_pin_sample_without_armature_matches_source_export_mesh(blender_env):
    module = blender_env.solver_test

    class SourceVertices:
        def __len__(self):
            return 2

        def foreach_get(self, _attribute, target):
            target[:] = (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)

    class EvaluatedVertices:
        def __len__(self):
            return 4

    source_vertices = SourceVertices()
    evaluated = SimpleNamespace(
        data=SimpleNamespace(vertices=EvaluatedVertices()),
        matrix_world=((1.0, 0.0, 0.0, 10.0),
                      (0.0, 1.0, 0.0, 0.0),
                      (0.0, 0.0, 1.0, 0.0),
                      (0.0, 0.0, 0.0, 1.0)))
    obj = SimpleNamespace(
        name="Unrigged Cloth", modifiers=(),
        data=SimpleNamespace(vertices=source_vertices),
        evaluated_get=lambda _depsgraph: evaluated)
    membership = SimpleNamespace(
        source_vertex_count=2, vertex_indices=(1,))

    positions = module._sample_evaluated_pin_positions(
        SimpleNamespace(evaluated_depsgraph_get=lambda: object()),
        obj, membership)

    assert positions == ((14.0, 6.0, -5.0),)


def test_pin_capture_pump_reuses_frame_depsgraph_without_extra_update(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    depsgraph = object()
    frames = []
    scene = SimpleNamespace(
        frame_current=1,
        frame_set=lambda frame, subframe=0.0:
            frames.append((frame, subframe)))
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
                        lambda _context, **_kwargs: (force_state, frozenset()))
    monkeypatch.setattr(module.shared_controller, "update", lambda **_kwargs: None)
    collider_calls = []
    monkeypatch.setattr(
        module, "_pump_collider_point",
        lambda passed_graph, point, states:
            collider_calls.append((passed_graph, point.position, states))
            or (1, 1, 0))

    collider_states = {"Collider": object()}
    module._pin_capture = {
        "context": context, "targets": (("Skirt", membership),),
        "range": SimpleNamespace(start=1, end=2),
        "points": module.build_sample_plan(
            1, 2, collider_samples=(4,)),
        "point_index": 0,
        "samples": {"Skirt": []}, "index_arrays": {"Skirt": indices},
        "force_samples": [], "active_scalar_types": set(),
        "force_capture": None, "collider_states": collider_states,
        "snapshot": SimpleNamespace(timings={}),
    }
    try:
        assert module._pin_capture_pump() == 0.005
        assert module._pin_capture_pump() == 0.005
        assert frames == [(1, 0.0), (1, 0.25)]
        assert calls == [
            (context, obj, membership, depsgraph, indices),
            (context, obj, membership, depsgraph, indices)]
        assert collider_calls == [
            (depsgraph, 1, collider_states),
            (depsgraph, module._pin_capture["points"][1].position,
             collider_states)]
        assert [sample.blender_frame for sample in
                module._pin_capture["samples"]["Skirt"]] == [1.0, 1.25]
        assert module._pin_capture["point_index"] == 2
        # Force animation remains frame-sampled; only Pins share the dense
        # Collider timeline.
        assert module._pin_capture["force_samples"] == [force_state]
    finally:
        module._pin_capture = None


def test_early_scene_identity_rejects_constraints_and_drivers(blender_env):
    module = blender_env.solver_test
    data = SimpleNamespace(
        name="Mesh", name_full="Mesh", library=None, shape_keys=None,
        animation_data=None)
    constrained = SimpleNamespace(
        name="Constrained", data=data, constraints=(object(),),
        animation_data=None, modifiers=(), parent=None)
    safe, _identity, reason = module._safe_object_dependency_identity(
        constrained)
    assert not safe and "constraint" in reason

    driven = SimpleNamespace(
        name="Driven", data=data, constraints=(), modifiers=(), parent=None,
        animation_data=SimpleNamespace(
            action=None, drivers=(object(),), nla_tracks=()))
    safe, _identity, reason = module._safe_object_dependency_identity(driven)
    assert not safe and "Driver" in reason


def test_simple_force_fcurve_bypasses_timeline(blender_env, monkeypatch):
    module = blender_env.solver_test
    curve = SimpleNamespace(
        data_path="cloth_next.force.strength", array_index=0,
        modifiers=(), evaluate=lambda frame: float(frame),
        keyframe_points=())
    action = SimpleNamespace(fcurves=(curve,))
    force = SimpleNamespace(
        force_type="WIND", strength=1.0, wind_variation=0.0)
    obj = SimpleNamespace(
        name="Wind", constraints=(), parent=None,
        animation_data=SimpleNamespace(
            action=action, drivers=(), nla_tracks=()),
        cloth_next=SimpleNamespace(force=force),
        matrix_world=((1.0, 0.0, 0.0, 0.0),
                      (0.0, 1.0, 0.0, 0.0),
                      (0.0, 0.0, 1.0, 0.0),
                      (0.0, 0.0, 0.0, 1.0)))
    scene = SimpleNamespace(
        use_gravity=False, gravity=(0.0, 0.0, -9.81),
        render=SimpleNamespace(fps=24))
    context = SimpleNamespace(scene=scene)
    monkeypatch.setattr(module, "_enabled_force_objects",
                        lambda _context: (obj,))

    capture = module._capture_simple_force_fcurves(
        context, module.BakeFrameRange(1, 3))

    assert capture is not None
    assert capture.initial.wind == (0.0, 0.0, 1.0)
    assert capture.dynamic_parameters


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


def test_cancel_during_pin_capture_does_not_continue_startup(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    restored = []
    cleaned = []
    monkeypatch.setattr(
        module, "_restore_pin_capture_state",
        lambda state: restored.append(state))
    monkeypatch.setattr(
        module, "_cleanup_collider_pump",
        lambda states: cleaned.append(states))
    monkeypatch.setattr(
        module, "_continue_production_bake",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("cancelled capture continued into startup")))
    state = {
        "context": SimpleNamespace(scene=SimpleNamespace()),
        "collider_states": {"Collider": object()},
    }
    module._pin_capture = state
    module._pending_job_id = ""
    module._cancel_event.set()
    module.shared_controller.transition(BakeState.PREPARING)
    module.shared_controller.request_cancel()
    try:
        assert module._pin_capture_pump() is None
        assert module._pin_capture is None
        assert restored == [state]
        assert cleaned == [state["collider_states"]]
        snapshot = module.shared_controller.snapshot()
        assert snapshot.state is BakeState.CANCELLED
        assert snapshot.status_message == (
            "Bake cancelled before a recovery checkpoint was available")
    finally:
        module._cancel_event.clear()
        if module.shared_controller.snapshot().state is not BakeState.IDLE:
            module.shared_controller.reset()


def test_async_collider_pump_keeps_canonical_schema2_timeline(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    collider = SimpleNamespace(
        name="Animated Collider",
        cloth_next=SimpleNamespace(
            collider_samples_per_frame=8,
            collider_motion_capture="TRANSFORM_ONLY"))

    states = module._begin_collider_pump(
        (collider,), module.BakeFrameRange(1, 64), 30.0)
    state = states[collider.name]
    metadata = state["metadata"]

    assert state["sample_count"] == 505
    assert metadata["_logical_frame_count"] == 64
    assert metadata["_samples_per_frame"] == 8
    assert metadata["_capture_fps"] == 30.0
    assert metadata["_sample_frame_offset"][:9] == [
        0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]
    assert metadata["_sample_frame_offset"][-1] == 63.0
    assert metadata["time"][-1] == pytest.approx(2.1)

    state["vertices"] = (
        (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    state["triangles"] = ((0, 1, 2),)
    state["matrices"] = [
        ((1.0, 0.0, 0.0, offset),
         (0.0, 1.0, 0.0, 0.0),
         (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0))
        for offset in metadata["_sample_frame_offset"]]
    monkeypatch.setattr(
        module, "_matrix_trs",
        lambda matrix: (
            [matrix[0][3], matrix[1][3], matrix[2][3]],
            [1.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0]))
    capture = module._finish_collider_pump(states)[collider.name]
    assert capture.content_digest
    animation = module.SceneObject(
        collider.name, "async-collider", capture.vertices,
        capture.triangles, capture.transform,
        transform_animation=capture.animation).info_dict(
            schema_version=2)["transform_animation"]
    assert animation["frame_offset"] == list(range(64))
    assert len(animation["translation"]) == 64
    assert animation["translation"][1][0] == pytest.approx(1.0)
    assert animation["translation"][-1][0] == pytest.approx(63.0)


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


def test_multi_worker_does_not_mislabel_solver_failure_as_cache_failure(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test

    class FailingSession:
        def __init__(self, **_kwargs):
            pass

        def run(self):
            raise RuntimeError("control server disappeared")

    monkeypatch.setattr(module, "SolverSession", FailingSession)
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    targets = tuple(
        module.DeformablePlan(
            ((float(index), 0.0, 0.0),), identity, name, uuid,
            tmp_path / f"{name}.pc2", f"topology-{name}", {}, "CLOTH")
        for index, (name, uuid) in enumerate(
            (("A", "uuid-a"), ("B", "uuid-b"))))
    plan = module.RunPlan(
        SimpleNamespace(cloth_uuid="uuid-a"), SimpleNamespace(),
        targets[0].initial_local, identity, "A", tmp_path / "run",
        targets[0].pc2_path, 2, deformables=targets)

    module._worker_main_multi(plan)

    message = module._queue.get_nowait()
    assert message[0] == "error"
    assert message[1] == "The solver session failed unexpectedly."
    assert "multi-object playback caches" not in message[1]
    assert "control server disappeared" in message[2]


def test_multi_worker_keeps_cache_message_for_real_writer_failure(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    monkeypatch.setattr(
        module.pc2, "StreamingPc2Writer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("cache directory is read-only")))
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    targets = tuple(
        module.DeformablePlan(
            ((float(index), 0.0, 0.0),), identity, name, uuid,
            tmp_path / f"{name}.pc2", f"topology-{name}", {}, "CLOTH")
        for index, (name, uuid) in enumerate(
            (("A", "uuid-a"), ("B", "uuid-b"))))
    plan = module.RunPlan(
        SimpleNamespace(cloth_uuid="uuid-a"), SimpleNamespace(),
        targets[0].initial_local, identity, "A", tmp_path / "run",
        targets[0].pc2_path, 2, deformables=targets)

    module._worker_main_multi(plan)

    message = module._queue.get_nowait()
    assert message[0] == "error"
    assert message[1] == "Creating the multi-object playback caches failed."
    assert "cache directory is read-only" in message[2]


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
    assert "What to do: Lower Friction first." in details
    assert details.index("Friction") < details.index("Time Step")


def test_solver_self_intersection_failure_is_concise(blender_env):
    module = blender_env.solver_test
    plan = SimpleNamespace(frame_start=1)
    long_tail = "trace line; " * 200
    error = ClothNextError(ErrorRecord.create(
        category=ErrorCategory.SIMULATION,
        user_message="The solver rejected the status request.",
        technical_message=(
            "server error during status: ValidationError: "
            "20 self-intersections (20 tri-tri); stdout_tail=("
            f"{long_tail})"),
        recommended_action="Inspect the logs."))

    summary, details = module._present_worker_error(plan, error)

    assert summary == "Intersections detected (20)."
    assert "20 self-intersecting triangle pairs" in details
    assert "Run Validate" in details
    assert "stdout_tail" not in details
    assert long_tail not in details


def test_ccd_failure_is_artist_friendly_and_keeps_log_tail_out_of_ui(
        blender_env):
    module = blender_env.solver_test
    plan = SimpleNamespace(frame_start=1)
    long_tail = "solver trace line\n" * 300
    error = ClothNextError(ErrorRecord.create(
        category=ErrorCategory.SIMULATION,
        user_message="The solver rejected the status request.",
        technical_message=(
            "server error during status: Continuous collision detection "
            "failed: advance failed at frame 1 (ccd=false, pcg=true, "
            "intersection_free=true); owned_process_id=46032; "
            f"stdout_tail=({long_tail}); num_contact: 13560"),
        recommended_action="Inspect the diagnostic log."))

    summary, details = module._present_worker_error(plan, error)

    assert summary == "Simulation could not advance at Blender frame 2."
    assert "continuous collision detection" in details
    assert "smaller Time Step" in details
    assert "stdout_tail" not in details
    assert "solver trace line" not in details
    assert len(details) < 1000


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


def test_force_capture_logs_initial_gravity_and_each_change(blender_env,
                                                            monkeypatch):
    module = blender_env.solver_test
    messages = []
    monkeypatch.setattr(
        module, "log_with_context",
        lambda _logger, _level, message, context=None:
            messages.append((message, context)))
    samples = (
        module.ForceState((0.0, 0.0, -9.81), (0.0, 0.0, 0.0)),
        module.ForceState((0.0, 0.0, -9.81), (0.0, 0.0, 0.0)),
        module.ForceState((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        module.ForceState((0.0, 0.0, -4.0), (0.0, 0.0, 0.0)),
    )

    module._log_gravity_capture(samples, module.BakeFrameRange(58, 61))

    assert messages == [
        ("Effective gravity captured", {
            "blender_frame": 58,
            "gravity_blender_xyz": (0.0, 0.0, -9.81),
        }),
        ("Effective gravity captured", {
            "blender_frame": 60,
            "gravity_blender_xyz": (0.0, 0.0, 0.0),
        }),
        ("Effective gravity captured", {
            "blender_frame": 61,
            "gravity_blender_xyz": (0.0, 0.0, -4.0),
        }),
    ]


def test_wind_strength_has_bounded_reproducible_randomized_gusts(blender_env):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    settings = SimpleNamespace(
        force_type="WIND", strength=2.0, wind_variation=0.5,
        air_density=0.001, air_friction=0.2, vertex_air_damp=0.0)
    wind = SimpleNamespace(
        name="Gusty Wind", name_full="Gusty Wind", type="EMPTY",
        matrix_world=identity,
        cloth_next=SimpleNamespace(enabled=True, role="FORCE", force=settings))

    class Scene:
        objects = (wind,)
        gravity = (0.0, 0.0, -9.81)
        use_gravity = True
        frame_current = 20
        render = SimpleNamespace(fps=24)

        def frame_set(self, frame, **_kwargs):
            self.frame_current = frame

    context = SimpleNamespace(scene=Scene(), view_layer=None)
    bake_range = module.BakeFrameRange(1, 20)
    first = module._capture_force_animation(context, bake_range)
    second = module._capture_force_animation(context, bake_range)
    values = [sample[1][2] for sample in first.dynamic_parameters[0][1]]

    assert first == second
    assert len(set(values)) > 10
    assert min(values) >= 1.5
    assert max(values) <= 2.5

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
    assert old.cache_format == "PC2"
    assert old.play_mode == "SCENE"
    assert old.deform_mode == "OVERWRITE"
    assert old.forward_axis == "POS_Y"
    assert old.up_axis == "POS_Z"


def test_attach_places_cache_after_armature_and_before_later_modifiers(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    armature = obj.modifiers.new("Armature", "ARMATURE")
    subdivision = obj.modifiers.new("Subdivision", "SUBSURF")
    path = tmp_path / "cn_test_cloth_new.pc2"
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
                          ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
                          obj.name, tmp_path, path, 1)

    module._attach_playback(plan, header)

    assert obj.modifiers[0] is armature
    assert module.has_cloth_next_playback_marker(obj, obj.modifiers[1])
    assert obj.modifiers[2] is subdivision


def test_attach_places_cache_after_last_armature(blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    first_rig = obj.modifiers.new("Primary Rig", "ARMATURE")
    second_rig = obj.modifiers.new("Corrective Rig", "ARMATURE")
    subdivision = obj.modifiers.new("Subdivision", "SUBSURF")
    path = tmp_path / "cn_test_cloth_new.pc2"
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    plan = module.RunPlan(SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
                          ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)),
                          obj.name, tmp_path, path, 1)

    module._attach_playback(plan, header)

    assert list(obj.modifiers[:2]) == [first_rig, second_rig]
    assert module.has_cloth_next_playback_marker(obj, obj.modifiers[2])
    assert obj.modifiers[3] is subdivision


def test_playback_stack_index_is_first_without_armature(blender_env):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    subdivision = obj.modifiers.new("Subdivision", "SUBSURF")
    cache = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")

    assert module._playback_stack_index(obj, cache) == 0
    assert obj.modifiers[0] is subdivision  # helper itself never mutates the stack


def test_modifier_lookup_accepts_rewrapped_blender_rna(blender_env):
    module = blender_env.solver_test

    class ModifierWrapper:
        def __init__(self, pointer, modifier_type="MESH_CACHE"):
            self._pointer = pointer
            self.type = modifier_type

        def as_pointer(self):
            return self._pointer

    stored = ModifierWrapper(0xC10)
    rewrapped = ModifierWrapper(0xC10)
    obj = SimpleNamespace(modifiers=[stored])

    assert stored is not rewrapped
    assert module._modifier_index(obj, rewrapped) == 0
    assert module._playback_stack_index(obj, rewrapped) == 0


def test_sewing_detection_uses_only_edges_without_faces(blender_env):
    module = blender_env.solver_test
    mesh = SimpleNamespace(
        polygons=[SimpleNamespace(vertices=(0, 1, 2))],
        edges=[SimpleNamespace(vertices=(0, 1)),
               SimpleNamespace(vertices=(1, 2)),
               SimpleNamespace(vertices=(2, 0)),
               SimpleNamespace(vertices=(1, 3)),
               SimpleNamespace(vertices=(4, 5))])

    pairs, hanging = module._detect_sewing_edges(mesh)

    assert pairs == ((1, 3), (4, 5))
    assert hanging == (3, 4, 5)


def test_sewing_post_snap_closes_only_pairs_within_contact_range(blender_env):
    module = blender_env.solver_test
    positions = ((0.0, 0.0, 0.0), (0.001, 0.0, 0.0),
                 (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))

    closed = module._snap_closed_sewing_pairs(
        positions, ((0, 1), (2, 3)), 0.01)

    assert tuple(closed[0]) == tuple(closed[1]) == (0.0005, 0.0, 0.0)
    assert tuple(closed[2]) == (1.0, 0.0, 0.0)
    assert tuple(closed[3]) == (2.0, 0.0, 0.0)


def test_animated_collider_samples_are_dense_and_include_exact_endpoints(
        blender_env):
    module = blender_env.solver_test
    points = module._collider_sample_points(
        module.BakeFrameRange(10, 11), 24)
    assert len(points) == 9
    assert points[0] == (10, 0.0, 0.0)
    assert points[-1] == (11, 0.0, 1.0 / 24.0)
    assert points[1] == (10, 0.125, 1.0 / 192.0)
    assert module._collider_sample_points(
        module.BakeFrameRange(1, 2), 24, 1) == (
            (1, 0.0, 0.0), (2, 0.0, 1.0 / 24.0))


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


def test_auto_collider_capture_is_conservative(blender_env):
    module = blender_env.solver_test
    clean = SimpleNamespace(
        data=SimpleNamespace(shape_keys=None, animation_data=None),
        modifiers=(),
        cloth_next=SimpleNamespace(collider_capture_mode="AUTO"))
    assert module._effective_collider_capture_mode(clean) == "TRANSFORM_ONLY"

    unknown_modifier = SimpleNamespace(type="NODES", show_viewport=True)
    uncertain = SimpleNamespace(
        data=SimpleNamespace(shape_keys=None, animation_data=None),
        modifiers=(unknown_modifier,),
        cloth_next=SimpleNamespace(collider_capture_mode="AUTO"))
    assert module._effective_collider_capture_mode(uncertain) == "DEFORMING"


def test_explicit_transform_only_rejects_known_deformation(blender_env):
    module = blender_env.solver_test
    collider = SimpleNamespace(
        name="Deforming Collider",
        data=SimpleNamespace(shape_keys=object(), animation_data=None),
        modifiers=(),
        cloth_next=SimpleNamespace(collider_capture_mode="TRANSFORM_ONLY"))
    with pytest.raises(module.SceneValidationError, match="Transform Only"):
        module._effective_collider_capture_mode(collider)


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


def test_cancelled_multi_object_prefix_is_published_for_every_target(
        blender_env, tmp_path):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    targets = []
    writers = {}
    for index in range(2):
        path = tmp_path / f"cn_test_cloth_{index}.pc2"
        meta = _phase4_meta()
        meta["expected"]["frame_count"] = 4
        target = module.DeformablePlan(
            ((0, 0, 0),), identity, f"cloth-{index}", f"uuid-{index}",
            path, "topology", meta, "CLOTH")
        targets.append(target)
        writer = pc2.StreamingPc2Writer(
            path, vertex_count=1, frame_count=4,
            resume_path=tmp_path / f"{target.uuid}.partial")
        writer.write_frame([[0, 0, 0]])
        writer.write_frame([[index + 1, 0, 0]])
        writers[target.uuid] = writer
    first = targets[0]
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), first.initial_local, identity,
        first.object_name, tmp_path, first.pc2_path, 4,
        frame_start=10, frame_end=13,
        settings_fingerprint="settings", geometry_fingerprint="geometry",
        material_meta=first.material_meta, deformables=tuple(targets))

    headers = module._publish_cancelled_previews(plan, writers, {})

    assert set(headers) == {"uuid-0", "uuid-1"}
    assert {header.frame_count for header in headers.values()} == {2}
    for target in targets:
        assert pc2.read_header(target.pc2_path).frame_count == 2
        assert writers[target.uuid].temporary_path.exists()
        inspection = cache_metadata.inspect_cache(
            target.pc2_path, settings_fingerprint="settings",
            geometry_fingerprint="geometry")
        assert inspection.usable
        assert inspection.metadata["details"]["partial_result"] == {
            "cached_frame_count": 2,
            "requested_frame_count": 4,
            "cancelled": True,
        }


def test_cancelled_cache_without_solver_frame_is_not_published(
        blender_env, tmp_path):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    path = tmp_path / "cn_test_cloth.pc2"
    target = module.DeformablePlan(
        ((0, 0, 0),), identity, "cloth", "uuid", path,
        "topology", _phase4_meta(), "CLOTH")
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), target.initial_local, identity,
        target.object_name, tmp_path, path, 4, deformables=(target,))
    writer = pc2.StreamingPc2Writer(
        path, vertex_count=1, frame_count=4,
        resume_path=tmp_path / "uuid.partial")
    writer.write_frame([[0, 0, 0]])

    assert module._publish_cancelled_previews(
        plan, {"uuid": writer}, {}) is None
    assert not path.exists()
    writer.abort()


def test_cancelled_preview_uses_shortened_plan_for_attachment(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),), identity,
        "cloth", SimpleNamespace(), SimpleNamespace(), 8,
        frame_start=12, frame_end=19)
    observed = []
    monkeypatch.setattr(
        module, "_attach_playback",
        lambda partial, header: observed.append((partial, header)))
    headers = {
        "a": SimpleNamespace(frame_count=3),
        "b": SimpleNamespace(frame_count=3),
    }

    assert module._attach_cancelled_preview(plan, headers) == 3
    assert observed[0][0].frame_count == 3
    assert observed[0][0].frame_end == 14


def test_configure_recovery_after_restart_selects_latest_checkpoint_and_rejects(
        blender_env, tmp_path):
    module = blender_env.solver_test
    settings = SimpleNamespace(
        enabled=True, resume_requested=False, keep_saved_states=3,
        save_on_cancel=True, save_on_finish=False,
        status="", status_detail="", compatible=False, resumable=False,
        recovery_directory="")
    context = SimpleNamespace(
        scene=SimpleNamespace(cloth_next_recovery=settings))
    identity_matrix = ((1, 0, 0, 0), (0, 1, 0, 0),
                       (0, 0, 1, 0), (0, 0, 0, 1))
    path = tmp_path / "cn_test_cloth.pc2"
    scene = module.SessionScene(
        "fresh", "Cloth", "cloth", 1, "Collider", "collider", 8,
        b"scene", b"param", "data-hash", "param-hash")
    plan = module.RunPlan(
        scene,
        SimpleNamespace(
            package_version="0.1.0", protocol_version="0.13",
            schema_version="2", installation_id="solver",
            installation=None),
        ((0, 0, 0),), identity_matrix, "Cloth", tmp_path, path, 8,
        frame_start=1, frame_end=8, fps=24.0,
        geometry_fingerprint="geometry", topology_signature="topology",
        material_meta=_phase4_meta(), scene_cache_key="scene-key",
        param_cache_key="param-with-pin-and-time-scale")
    snapshot = SimpleNamespace(collider_objs=())
    configured = module._configure_recovery(context, snapshot, plan)
    options = configured.recovery_options
    project_root = options.server_data_root / "saved-project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True)
    record = module.recovery.create_project(
        options.metadata_path, project_id="saved-project",
        identity=options.identity, server_data_root=options.server_data_root,
        project_root=project_root, partial_pc2=options.partial_pc2)
    record = module.recovery.transition(
        options.metadata_path, record, module.recovery.ProjectState.RUNNING)
    for frame in (3, 6):
        (output / f"state_{frame}.bin.gz").write_bytes(
            __import__("gzip").compress(f"state-{frame}".encode()))
    record = module.recovery.confirm_saved_states(
        options.metadata_path, record, (3, 6), keep=3)
    record = module.recovery.transition(
        options.metadata_path, record, module.recovery.ProjectState.SAVED)
    module.recovery.transition(
        options.metadata_path, record, module.recovery.ProjectState.RESUMABLE)

    settings.resume_requested = True
    resumed = module._configure_recovery(context, snapshot, plan)

    assert resumed.recovery_options.resume
    assert resumed.recovery_options.resume_from_frame == 6
    assert resumed.scene.project_name == "saved-project"

    settings.resume_requested = True
    changed = module.replace(
        plan, scene=module.replace(
            plan.scene, param_hash="changed-pin-or-time-scale"))
    with pytest.raises(module.SceneValidationError, match="Pin.*Time Scale"):
        module._configure_recovery(context, snapshot, changed)
    assert not settings.resume_requested


def test_configure_recovery_uses_payload_hash_when_early_cache_is_unsafe(
        blender_env, tmp_path):
    module = blender_env.solver_test
    settings = SimpleNamespace(
        enabled=True, resume_requested=False, keep_saved_states=3,
        save_on_cancel=True, save_on_finish=False,
        status="", status_detail="", compatible=False, resumable=False,
        recovery_directory="")
    context = SimpleNamespace(
        scene=SimpleNamespace(cloth_next_recovery=settings))
    scene = module.SessionScene(
        "fresh", "Cloth", "cloth", 1, "Collider", "collider", 4,
        b"captured-shape-key-scene", b"param",
        "captured-scene-hash", "param-hash")
    plan = module.RunPlan(
        scene,
        SimpleNamespace(
            package_version="0.1.0", protocol_version="0.13",
            schema_version="2", installation_id="solver",
            installation=None),
        ((0, 0, 0),),
        ((1, 0, 0, 0), (0, 1, 0, 0),
         (0, 0, 1, 0), (0, 0, 0, 1)),
        "Cloth", tmp_path, tmp_path / "cloth.pc2", 4,
        frame_start=1, frame_end=4, fps=24.0,
        geometry_fingerprint="geometry", topology_signature="topology",
        material_meta=_phase4_meta(), scene_cache_key="",
        param_cache_key="param-key")

    configured = module._configure_recovery(
        context, SimpleNamespace(collider_objs=()), plan)

    assert configured.recovery_options is not None
    assert configured.recovery_options.identity.scene_key == (
        "captured-scene-hash")
    assert configured.recovery_options.identity.param_key == "param-hash"


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


def test_solver_failure_attaches_valid_prefix_before_reporting_error(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    module.shared_controller.transition(BakeState.PREPARING)
    module.shared_controller.transition(BakeState.EXPORTING)
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    plan = module.RunPlan(
        module.SessionScene(
            "project", "Cloth", "cloth", 1, "Collider", "collider", 8,
            b"scene", b"param", "data", "param"),
        SimpleNamespace(), ((0, 0, 0),), identity, "Cloth",
        SimpleNamespace(), SimpleNamespace(), 8,
        frame_start=10, frame_end=17)
    module._active_plan = plan
    module._worker = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(
        module, "_attach_cancelled_preview",
        lambda _plan, _preview: 3)
    monkeypatch.setattr(module, "_discard_incomplete", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "_refresh_recovery_ui", lambda *_a: None)
    while not module._queue.empty():
        module._queue.get_nowait()
    module._queue.put((
        "error", "The solver connection was lost.", "socket closed",
        "CNX-E101", (), object()))

    assert module._pump_once() is None
    snapshot = module.shared_controller.snapshot()
    assert snapshot.state is BakeState.ERROR
    assert snapshot.current_frame == 12
    assert snapshot.progress_current == 3
    assert snapshot.progress_total == 8
    assert "socket closed" in snapshot.error_details


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


@pytest.mark.parametrize(("kind_name", "resumable", "expected"), (
    ("SAVED", True, "Recovery checkpoint saved"),
    ("EXISTING_PRESERVED", True, "Existing recovery checkpoint preserved"),
    ("NOT_ENABLED", False, "Bake cancelled"),
    ("NOT_AVAILABLE_YET", False, "before a recovery checkpoint was available"),
    ("FAILED", False, "Recovery checkpoint could not be saved"),
))
def test_cancelled_outcome_category_controls_final_ui_message(
        blender_env, monkeypatch, kind_name, resumable, expected):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    module.shared_controller.transition(BakeState.PREPARING)
    module.shared_controller.transition(BakeState.EXPORTING)
    module.shared_controller.request_cancel()
    module._active_plan = SimpleNamespace()
    module._worker = SimpleNamespace(is_alive=lambda: True)
    module._ram_auto_cancel_enabled = False
    module._ram_auto_cancel_triggered = False
    monkeypatch.setattr(module, "_discard_incomplete", lambda *_a, **_k: None)
    monkeypatch.setattr(module, "_refresh_recovery_ui", lambda *_a: None)
    while not module._queue.empty():
        module._queue.get_nowait()
    kind = module.RecoveryOutcomeKind[kind_name]
    outcome = module.RecoveryOutcome(
        kind=kind, checkpoint_saved=resumable, artist_message="test",
        technical_reason=("details" if kind_name == "FAILED" else ""),
        state_before="BUSY", saved_states=((2,) if resumable else ()))
    module._queue.put(("cancelled", resumable, outcome))

    assert module._pump_once() is None
    snapshot = module.shared_controller.snapshot()
    assert snapshot.state is BakeState.CANCELLED
    assert expected in snapshot.status_message

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


def test_recovery_related_operators_have_useful_tooltips(blender_env):
    module = blender_env.solver_test
    classes = (
        module.CLOTHNEXT_OT_bake_cancel,
        module.CLOTHNEXT_OT_solver_test_cancel,
        module.CLOTHNEXT_OT_recovery_resume_latest,
        module.CLOTHNEXT_OT_recovery_start_fresh,
        module.CLOTHNEXT_OT_recovery_clear_checkpoints,
        module.CLOTHNEXT_OT_recovery_open_folder,
    )
    for operator in classes:
        assert operator.bl_description.strip()
    assert "latest verified" in (
        module.CLOTHNEXT_OT_recovery_resume_latest.bl_description)
    assert "cannot be resumed" in (
        module.CLOTHNEXT_OT_recovery_start_fresh.bl_description)
    assert "attempt" in module.CLOTHNEXT_OT_bake_cancel.bl_description
