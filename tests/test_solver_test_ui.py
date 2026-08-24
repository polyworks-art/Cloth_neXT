# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Threading, cancellation, and cleanup contracts for the Blender bridge."""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from tests import mesh_fixtures

from cloth_next import recovery
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


def test_solver_failure_preserves_only_valid_completed_frame_prefix(
        blender_env, tmp_path):
    module = blender_env.solver_test
    final = tmp_path / "cache.pc2"
    writer = pc2.StreamingPc2Writer(
        final, vertex_count=2, frame_count=5)
    frame = np.zeros((2, 3), dtype=np.float32)
    writer.write_frame(frame)
    writer.write_frame(frame + 1.0)
    writer.write_frame(frame + 2.0)

    result = module._preserve_failed_partial(writer)

    assert result is not None
    path, frames = result
    assert frames == 3
    assert path.is_file()
    assert pc2.partial_frame_count(path, writer.header) == 3
    assert not final.exists()


def test_solver_failure_discards_input_pose_without_completed_solver_frame(
        blender_env, tmp_path):
    module = blender_env.solver_test
    writer = pc2.StreamingPc2Writer(
        tmp_path / "cache.pc2", vertex_count=2, frame_count=5)
    writer.write_frame(np.zeros((2, 3), dtype=np.float32))
    temporary = writer.temporary_path

    assert module._preserve_failed_partial(writer) is None
    assert not temporary.exists()


def test_preserved_partial_is_reported_without_replacing_primary_error(
        blender_env, tmp_path):
    module = blender_env.solver_test
    primary = ClothNextError(ErrorRecord.create(
        category=ErrorCategory.SOLVER_CONNECTION,
        user_message="The solver exited while simulating frame 3.",
        technical_message="failure_kind=CRASH_DURING_SIMULATION",
        recommended_action="Retry.", recoverable=True))
    partial = tmp_path / "partial.pc2"
    partial.write_bytes(b"validated")

    enriched = module._with_preserved_partial_error(
        primary, (("cloth", partial, 3),))

    assert enriched.record.user_message.startswith(
        "The solver exited while simulating frame 3.")
    assert enriched.record.user_message.endswith(
        "Completed frames were preserved.")
    assert "validated_partial_pc2" in enriched.record.technical_message
    assert "partial_frame_counts=(3,)" in enriched.record.technical_message


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


def test_static_collider_animation_detection_and_bake_guard(blender_env):
    module = blender_env.solver_test
    curve = SimpleNamespace(keyframe_points=(SimpleNamespace(),))
    action = SimpleNamespace(fcurves=(curve,))
    collider = SimpleNamespace(
        name="Moving Collider",
        animation_data=SimpleNamespace(action=action, drivers=()),
        constraints=(), data=SimpleNamespace(animation_data=None, shape_keys=None),
        parent=None, modifiers=(),
        cloth_next=SimpleNamespace(collider_motion="STATIC"))

    assert module.static_collider_has_animation(collider)
    with pytest.raises(module.SceneValidationError, match="Motion.*Static"):
        module._reject_animated_static_colliders((collider,))

    collider.cloth_next.collider_motion = "ANIMATED"
    module._reject_animated_static_colliders((collider,))


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


def test_contact_validation_worker_never_creates_or_writes_frame_cache(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    calls = []

    class StubSession:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs.get("frame_sink"),
                          kwargs.get("recovery_options")))

        def validate_contacts(self):
            calls.append(("validate",))
            return SimpleNamespace(timings={}, cache_events={})

    monkeypatch.setattr(module, "SolverSession", StubSession)
    path = tmp_path / "must-not-exist.pc2"
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0.0, 0.0, 0.0),),
        ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        "cloth", tmp_path / "run", path, 1)

    module._contact_validation_worker(plan)

    message = module._queue.get_nowait()
    assert message[0] == "contact_validated"
    assert calls == [("init", None, None), ("validate",)]
    assert not path.exists()


def test_initial_contact_build_failure_never_constructs_frame_writer(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test

    class FailingBuildSession:
        def __init__(self, **_kwargs):
            pass

        def run(self):
            raise ClothNextError(ErrorRecord.create(
                category=ErrorCategory.SIMULATION,
                user_message="Initial self intersection",
                technical_message="contact BUILD failed before START"))

    monkeypatch.setattr(module, "SolverSession", FailingBuildSession)
    monkeypatch.setattr(
        module.pc2, "StreamingPc2Writer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("frame writer created before clean contact build")))
    path = tmp_path / "must-not-exist.pc2"
    plan = module.RunPlan(
        SimpleNamespace(cloth_uuid="cloth"), SimpleNamespace(),
        ((0.0, 0.0, 0.0),),
        ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        "cloth", tmp_path / "run", path, 2)

    module._worker_main(plan)

    assert module._queue.get_nowait()[0] == "error"
    assert not path.exists()


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
    class ProducingSession:
        def __init__(self, **kwargs):
            self.sink = kwargs["frame_sink"]

        def run(self):
            positions = {
                "uuid-a": np.asarray(((0.0, 0.0, 0.0),), dtype=np.float32),
                "uuid-b": np.asarray(((1.0, 0.0, 0.0),), dtype=np.float32)}
            self.sink(module.SolverFrame(1, positions["uuid-a"], positions))

    monkeypatch.setattr(module, "SolverSession", ProducingSession)
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


def test_solver_violations_fall_back_to_recovery_project_sidecar(
        blender_env, tmp_path):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    source = SimpleNamespace(
        uuid="cloth", name="Cloth",
        vertices_local=((0, 0, 0), (1, 0, 0), (0, 1, 0),
                        (1, 1, 0)),
        triangles=((0, 1, 2), (1, 3, 2)), transform=identity)
    snapshot = module.intersection_diagnostics.build_solver_input_snapshot(
        ((source, "CLOTH", (7, 8), False),), bake_start_frame=1)
    recovery_root = tmp_path / "recovery-server"
    project_root = recovery_root / "recovered-project"
    project_root.mkdir(parents=True)
    (project_root / "build_violations.json").write_text(
        json.dumps({"violations": [{"combined_pair": [0, 1]}]}),
        encoding="utf-8")
    plan = SimpleNamespace(
        solver_input=snapshot,
        work_directory=tmp_path / "new-run",
        scene=SimpleNamespace(project_name="recovered-project"),
        recovery_options=SimpleNamespace(server_data_root=recovery_root))
    error = ClothNextError(ErrorRecord.create(
        category=ErrorCategory.SIMULATION,
        user_message="The solver rejected the build.",
        technical_message="build validation failed",
        recommended_action="Inspect the highlighted faces."))

    converted = module._convert_solver_violations(plan, error)

    assert error.violations == ()
    assert len(converted) == 1
    assert converted[0].combined_pair == (0, 1)
    assert converted[0].classification == "SELF_INTERSECTION"
    assert [item.source_polygon_index
            for item in converted[0].elements] == [7, 8]


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


def test_unified_force_values_are_applied_together(blender_env):
    module = blender_env.solver_test
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    settings = SimpleNamespace(
        force_type="GRAVITY", strength=9.81,
        gravity_strength=4.0, wind_strength=2.5, wind_variation=0.0,
        air_density=0.8, air_friction=0.3, vertex_air_damp=0.15)
    force = SimpleNamespace(
        name="Environment", name_full="Environment", type="EMPTY",
        matrix_world=identity,
        cloth_next=SimpleNamespace(enabled=True, role="FORCE", force=settings))
    context = SimpleNamespace(scene=SimpleNamespace(
        objects=(force,), gravity=(0.0, 0.0, -9.81), use_gravity=True))

    state, active = module._force_state(context)

    assert state.gravity == (0.0, 0.0, -4.0)
    assert state.wind == (0.0, 0.0, 2.5)
    assert state.air_density == pytest.approx(0.8)
    assert state.air_friction == pytest.approx(0.3)
    assert state.vertex_air_damp == pytest.approx(0.15)
    assert active == {"AIR_DENSITY", "AIR_FRICTION", "VERTEX_AIR_DAMP"}


def test_unified_gravity_axis_is_independent_of_empty_rotation(blender_env):
    module = blender_env.solver_test
    settings = SimpleNamespace(
        force_type="GRAVITY", strength=9.81, gravity_strength=6.0,
        gravity_axis="X_POS", wind_strength=0.0, wind_variation=0.0,
        air_density=0.01, air_friction=0.2, vertex_air_damp=0.0)
    # A degenerate local Z is valid when Wind is disabled: Gravity no longer
    # derives its direction from the Empty transform.
    force = SimpleNamespace(
        name="Gravity", name_full="Gravity", type="EMPTY",
        matrix_world=((1, 0, 0, 0), (0, 1, 0, 0),
                      (0, 0, 0, 0), (0, 0, 0, 1)),
        cloth_next=SimpleNamespace(enabled=True, role="FORCE", force=settings))
    context = SimpleNamespace(scene=SimpleNamespace(
        objects=(force,), gravity=(0.0, 0.0, -9.81), use_gravity=True))

    state, _active = module._force_state(context)

    assert state.gravity == (6.0, 0.0, 0.0)
    assert state.wind == (0.0, 0.0, 0.0)


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
    assert min(values) >= 2.0


def test_wind_gusts_are_aperiodic_across_short_and_long_time_scales(
        blender_env):
    module = blender_env.solver_test
    wind = SimpleNamespace(name="Natural Wind", name_full="Natural Wind")
    values = [module._wind_oscillation(wind, frame, 24.0)
              for frame in range(1, 24 * 30 + 1)]

    assert all(-1.0 <= value <= 1.0 for value in values)
    assert values == [module._wind_oscillation(wind, frame, 24.0)
                      for frame in range(1, 24 * 30 + 1)]
    # The old two-sine implementation repeated a conspicuous smooth rhythm.
    # Multi-scale value noise must vary both over adjacent seconds and over
    # wider ten-second windows.
    one_second = [values[index] for index in range(0, len(values), 24)]
    ten_second = [values[index] for index in range(0, len(values), 240)]
    assert len({round(value, 4) for value in one_second}) > 10
    assert len({round(value, 4) for value in ten_second}) == len(ten_second)


def test_wind_noise_scale_slows_gust_evolution(blender_env):
    module = blender_env.solver_test
    wind = SimpleNamespace(name="Scaled Wind", name_full="Scaled Wind")

    normal = module._wind_oscillation(wind, 48, 24.0, 1.0)
    slower = module._wind_oscillation(wind, 144, 24.0, 3.0)

    assert slower == pytest.approx(normal)


def test_wind_variation_produces_separated_positive_gusts(blender_env):
    module = blender_env.solver_test
    wind = SimpleNamespace(name="Gust Wind", name_full="Gust Wind")
    values = [module._wind_oscillation(wind, frame, 24.0, 3.0)
              for frame in range(1, 24 * 120 + 1)]

    assert min(values) == 0.0
    assert max(values) > 0.5
    assert sum(value == 0.0 for value in values) > len(values) * 0.2

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


def test_rebake_accepts_owned_live_preview_left_by_cancel(
        blender_env, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    live = tmp_path / ".cn_test_cloth_old.pc2.deadbeef.tmp"
    modifier = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    modifier.filepath = str(live)
    module.mark_owned_playback(obj, modifier, modifier.filepath)
    final = tmp_path / "cn_test_cloth_new.pc2"
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
        ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        obj.name, tmp_path, final, 1)

    module.prepare_cache_for_new_run(plan)


def test_resume_accepts_only_its_authenticated_recovery_partial(
        blender_env, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    partial = tmp_path / ".cloth_next_recovery" / "scene" / "partials" / "cloth.pc2.partial"
    modifier = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    modifier.filepath = str(partial)
    module.mark_owned_playback(obj, modifier, modifier.filepath)
    final = tmp_path / "cn_test_cloth_new.pc2"
    options = SimpleNamespace(partial_pc2=(("cloth-uuid", str(partial)),))
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
        ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)),
        obj.name, tmp_path, final, 1, recovery_options=options)

    module.prepare_cache_for_new_run(plan)


def _stale_recovery_partial(tmp_path, *, uuid="cloth-uuid"):
    old_cache = tmp_path / "old-cache"
    metadata = recovery.metadata_path(old_cache, "old-scene")
    partial = metadata.parent / "partials" / f"{uuid}.pc2.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"partial")
    project_root = tmp_path / "old-server" / "project"
    project_root.mkdir(parents=True)
    identity = recovery.RecoveryIdentity(
        scene_key="old-scene", param_key="old-param",
        export_uuids=(uuid,), geometry_fingerprint="old-geometry",
        topology_fingerprint="old-topology", frame_start=1, frame_end=60,
        fps=24.0, collider_sampling=(), solver_version="0.1.0",
        protocol_version="0.11", solver_schema_version="1",
        solver_installation_id="unregistered")
    recovery.create_project(
        metadata, project_id="old-project", identity=identity,
        server_data_root=tmp_path / "old-server",
        project_root=project_root,
        partial_pc2=((uuid, str(partial)),))
    return partial


def test_rebake_accepts_authenticated_partial_from_previous_recovery_identity(
        blender_env, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    old_partial = _stale_recovery_partial(tmp_path)
    modifier = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    modifier.filepath = str(old_partial)
    module.mark_owned_playback(obj, modifier, modifier.filepath)
    new_cache = tmp_path / "new-cache"
    new_partial = (new_cache / ".cloth_next_recovery" / "new-scene" /
                   "partials" / "cloth-uuid.pc2.partial")
    options = SimpleNamespace(
        partial_pc2=(("cloth-uuid", str(new_partial)),))
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
        ((1, 0, 0, 0), (0, 1, 0, 0),
         (0, 0, 1, 0), (0, 0, 0, 1)),
        obj.name, tmp_path, new_cache / "cn_test_cloth_new.pc2", 1,
        recovery_options=options)

    module.prepare_cache_for_new_run(plan)


def test_rebake_rejects_unauthenticated_foreign_recovery_partial(
        blender_env, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    foreign = (tmp_path / "foreign" / ".cloth_next_recovery" / "scene" /
               "partials" / "cloth-uuid.pc2.partial")
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"artist data")
    modifier = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")
    modifier.filepath = str(foreign)
    module.mark_owned_playback(obj, modifier, modifier.filepath)
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
        ((1, 0, 0, 0), (0, 1, 0, 0),
         (0, 0, 1, 0), (0, 0, 0, 1)),
        obj.name, tmp_path, tmp_path / "cache" / "cn_test_cloth_new.pc2", 1)

    with pytest.raises(module.SceneValidationError):
        module.prepare_cache_for_new_run(plan)


def test_finished_cache_is_exposed_as_timeline_strip(blender_env):
    module = blender_env.solver_test

    class Scene:
        def frame_set(self, frame):
            self.frame_current = frame

    scene = Scene()
    blender_env.bpy.context.scene = scene
    plan = SimpleNamespace(frame_start=12, frame_end=48)

    module._show_baked_timeline(plan)

    from cloth_next.blender import timeline_overlay
    assert not scene.use_preview_range
    assert timeline_overlay.baked_range() == (12, 48, 48)
    assert scene.frame_current == 12


def test_live_bake_timeline_advances_only_to_latest_completed_frame(blender_env):
    module = blender_env.solver_test

    class Scene:
        frame_current = 10

        def frame_set(self, frame):
            self.frame_current = frame

    scene = Scene()
    blender_env.bpy.context.scene = scene
    plan = SimpleNamespace(frame_start=10, frame_end=50)

    module._advance_bake_timeline(plan, 23)
    from cloth_next.blender import timeline_overlay
    assert scene.frame_current == 23
    assert not scene.use_preview_range
    assert timeline_overlay.baked_range() == (10, 23, 50)

    module._advance_bake_timeline(plan, 999)
    assert scene.frame_current == 50
    assert timeline_overlay.baked_range() == (10, 50, 50)


def test_solver_progress_moves_timeline_before_live_pc2_is_available(
        blender_env, monkeypatch):
    module = blender_env.solver_test

    class Scene:
        frame_current = 10
        use_preview_range = True

        def frame_set(self, frame):
            self.frame_current = frame

    scene = Scene()
    blender_env.bpy.context.scene = scene
    plan = SimpleNamespace(frame_start=10, frame_end=50)
    attached = []
    monkeypatch.setattr(module, "_attach_live_playback",
                        lambda *_args, **_kwargs: attached.append(True))

    module._advance_bake_progress(plan, 23)

    from cloth_next.blender import timeline_overlay
    assert scene.frame_current == 23
    assert timeline_overlay.baked_range() == (10, 23, 50)
    assert not scene.use_preview_range
    assert attached == []


def test_live_bake_attaches_private_growing_cache_before_timeline_advances(
        blender_env, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    final = tmp_path / "cloth.pc2"
    live = tmp_path / ".cloth.pc2.live.tmp"
    live.write_bytes(b"growing pc2")
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    target = module.DeformablePlan(
        ((0, 0, 0),), identity, obj.name, "cloth-uuid", final,
        "topology", {}, "CLOTH")
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), target.initial_local, identity,
        obj.name, tmp_path, final, 3, frame_start=10, frame_end=12,
        deformables=(target,))

    class Scene:
        frame_current = 10

        def frame_set(self, frame):
            assert obj.modifiers[0].filepath == str(live)
            self.frame_current = frame

    blender_env.bpy.context.scene = Scene()
    module._advance_bake_timeline(
        plan, 11, {target.uuid: str(live)})

    assert obj.modifiers[0].filepath == str(live)
    assert blender_env.bpy.context.scene.frame_current == 11


def test_rebake_live_progress_never_retargets_successful_generation(
        blender_env, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    old = tmp_path / "cn_test_cloth_generation_n.pc2"
    final = tmp_path / "cn_test_cloth_generation_n1.pc2"
    live = tmp_path / ".cn_test_cloth_generation_n1.pc2.live.tmp"
    old.write_bytes(b"successful generation")
    live.write_bytes(b"growing generation")
    modifier = obj.modifiers.new(module.import_result.MODIFIER_NAME,
                                 "MESH_CACHE")
    modifier.filepath = str(old)
    module.mark_owned_playback(obj, modifier, str(old))
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    target = module.DeformablePlan(
        ((0, 0, 0),), identity, obj.name, "cloth-uuid", final,
        "topology", {}, "CLOTH")
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), target.initial_local, identity,
        obj.name, tmp_path, final, 3, frame_start=10, frame_end=12,
        deformables=(target,))

    module._attach_live_playback(plan, {target.uuid: str(live)})

    assert len(obj.modifiers) == 1
    assert modifier.filepath == str(old)
    assert old.read_bytes() == b"successful generation"


def test_single_deformable_tuple_accepts_single_worker_header(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    path = tmp_path / "cn_test_cloth_single.pc2"
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    identity = ((1, 0, 0, 0), (0, 1, 0, 0),
                (0, 0, 1, 0), (0, 0, 0, 1))
    target = module.DeformablePlan(
        ((0, 0, 0),), identity, obj.name, "cloth-uuid", path,
        "topology", {}, "CLOTH")
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), target.initial_local, identity,
        obj.name, tmp_path, path, 1, deformables=(target,))

    module._attach_playback(plan, header)

    assert len(obj.modifiers) == 1
    assert obj.modifiers[0].filepath == str(path)


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


def test_playback_index_is_after_corrective_smooth(blender_env):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Corrected Cloth", type="MESH")
    armature = obj.modifiers.new("Armature", "ARMATURE")
    smooth = obj.modifiers.new("Corrective Smooth", "CORRECTIVE_SMOOTH")
    subdivision = obj.modifiers.new("Subdivision", "SUBSURF")
    solidify = obj.modifiers.new("Solidify", "SOLIDIFY")
    for modifier in obj.modifiers:
        modifier.show_viewport = True
    cache = obj.modifiers.new(module.import_result.MODIFIER_NAME, "MESH_CACHE")

    assert module._playback_stack_index(obj, cache) == 2
    assert [armature, smooth, subdivision, solidify] == list(obj.modifiers[:4])


def test_animated_collider_capture_cache_round_trip(
        blender_env, tmp_path):
    module = blender_env.solver_test
    cache = module.ExportPayloadCache(tmp_path)
    capture = module.ColliderMotionCapture(
        "RIGID_ANIMATED", ((0.0, 0.0, 0.0),), ((0, 0, 0),),
        tuple(tuple(1.0 if row == column else 0.0 for column in range(4))
              for row in range(4)),
        {"time": [0.0, 1.0], "translation": [[0, 0, 0], [1, 0, 0]],
         "quaternion": [[1, 0, 0, 0], [1, 0, 0, 0]],
         "scale": [[1, 1, 1], [1, 1, 1]], "segments": []},
        content_digest="motion")

    module._store_animated_collider_capture(cache, "a" * 64, capture)
    restored, reason = module._load_animated_collider_capture(cache, "a" * 64)

    assert reason == "verified"
    assert restored.motion_type == capture.motion_type
    assert restored.vertices == capture.vertices
    assert restored.animation["translation"] == capture.animation["translation"]


def test_animated_collider_cache_hit_skips_expensive_capture_list(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    cache = module.ExportPayloadCache(tmp_path)
    key = "c" * 64
    collider = SimpleNamespace(name="Collider")
    snapshot = SimpleNamespace(cloth_obj=object(), timings={})
    capture = module.ColliderMotionCapture(
        "RIGID_ANIMATED", ((0.0, 0.0, 0.0),), ((0, 0, 0),),
        tuple(tuple(1.0 if row == column else 0.0 for column in range(4))
              for row in range(4)), {"time": [0.0]}, content_digest="motion")
    module._store_animated_collider_capture(cache, key, capture)
    monkeypatch.setattr(module, "_payload_cache_for", lambda _obj: cache)
    monkeypatch.setattr(module, "_animated_collider_cache_key",
                        lambda *_args: (key, "safe collider identity"))

    hits, misses, keys, selected_cache = \
        module._load_cached_animated_colliders(
            SimpleNamespace(), snapshot, (collider,),
            module.BakeFrameRange(1, 2))

    assert tuple(hits) == ("Collider",)
    assert misses == ()  # _begin_collider_pump receives no Collider
    assert keys == {}
    assert selected_cache is cache
    assert snapshot.timings["animated_collider_cache_hits"] == 1.0
    assert snapshot.timings["animated_collider_cache_misses"] == 0.0
    assert snapshot.timings["collider_sample_count"] == 0.0


def test_animated_collider_cache_rejects_missing_artifact(
        blender_env, tmp_path):
    module = blender_env.solver_test
    cache = module.ExportPayloadCache(tmp_path)
    capture = module.ColliderMotionCapture(
        "RIGID_ANIMATED", ((0.0, 0.0, 0.0),), ((0, 0, 0),),
        tuple(tuple(1.0 if row == column else 0.0 for column in range(4))
              for row in range(4)), {"time": [0.0]}, content_digest="motion")
    key = "b" * 64
    module._store_animated_collider_capture(cache, key, capture)
    cache.lookup_artifacts("collider", key)["vertices.f32"].unlink()

    restored, reason = module._load_animated_collider_capture(cache, key)

    assert restored is None
    assert "artifact" in reason


def test_animated_collider_key_tracks_geometry_animation_range_and_fps(
        blender_env):
    module = blender_env.solver_test
    blender_env.registration.register()
    scene = mesh_fixtures.build_cloth_scene(blender_env.bpy, vertex_count=16)
    collider = scene.collider
    collider.cloth_next.collider_capture_mode = "AUTO"
    collider.cloth_next.collider_samples_per_frame = 2
    collider.cloth_next.collider_motion = "ANIMATED"
    collider.cloth_next.persistent_export_id = "collider-cache-test"
    bake_range = module.BakeFrameRange(1, 24)
    first, reason = module._animated_collider_cache_key(
        scene.context, collider, bake_range)
    assert first and reason == "safe collider identity"

    collider.data.arrays[("vertex_scans", "co")][0] = (3.0, 0.0, 0.0)
    geometry, _ = module._animated_collider_cache_key(
        scene.context, collider, bake_range)
    keyframe = SimpleNamespace(co=(1.0, 2.0), handle_left=(1.0, 2.0),
                               handle_right=(1.0, 2.0),
                               interpolation="LINEAR", easing="AUTO")
    curve = SimpleNamespace(data_path="location", array_index=0,
                            keyframe_points=(keyframe,), modifiers=())
    collider.animation_data = SimpleNamespace(action=SimpleNamespace(
        name="Move", name_full="Move", fcurves=(curve,), library=None),
        drivers=(), nla_tracks=())
    animation, _ = module._animated_collider_cache_key(
        scene.context, collider, bake_range)
    ranged, _ = module._animated_collider_cache_key(
        scene.context, collider, module.BakeFrameRange(2, 24))
    scene.context.scene.render.fps = 30
    timed, _ = module._animated_collider_cache_key(
        scene.context, collider, module.BakeFrameRange(2, 24))

    assert len({first, geometry, animation, ranged, timed}) == 5
    blender_env.registration.unregister()


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


def test_repeated_generation_swap_reclaims_old_cache_when_unlink_is_locked(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Kleid Überwurf", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    old_path = tmp_path / "缓存" / "cn_test_cloth_generation_a.pc2"
    new_path = tmp_path / "缓存" / "cn_test_cloth_generation_b.pc2"
    old_path.parent.mkdir()
    old_path.write_bytes(b"old generation")
    new_path.write_bytes(b"new generation")
    modifier = obj.modifiers.new(
        module.import_result.MODIFIER_NAME, "MESH_CACHE")
    modifier.filepath = str(old_path)
    module.mark_owned_playback(obj, modifier, modifier.filepath)
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    original_unlink = Path.unlink

    def locked_unlink(path, *args, **kwargs):
        if path == old_path:
            raise PermissionError("cache is mapped by Blender")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    plan = module.RunPlan(
        SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
        ((1, 0, 0, 0), (0, 1, 0, 0),
         (0, 0, 1, 0), (0, 0, 0, 1)),
        obj.name, tmp_path, new_path, 1)

    module._attach_playback(plan, header)

    assert old_path != new_path
    assert modifier.filepath == str(new_path)
    # A transiently locked obsolete generation is removed or tombstoned; the
    # canonical filename must no longer block later rebakes.
    assert not old_path.exists()
    assert new_path.read_bytes() == b"new generation"
    assert list(obj.modifiers) == [modifier]


def test_twelve_rebakes_reclaim_transiently_locked_obsolete_generations(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Repeated Cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    cache_root = tmp_path / "locked generations"
    cache_root.mkdir()
    first = cache_root / "cn_test_cloth_generation_00.pc2"
    first.write_bytes(b"generation 0")
    modifier = obj.modifiers.new(module.import_result.MODIFIER_NAME,
                                 "MESH_CACHE")
    modifier.filepath = str(first)
    module.mark_owned_playback(obj, modifier, modifier.filepath)
    header = SimpleNamespace(vertex_count=1, frame_count=1)
    monkeypatch.setattr(module.pc2, "read_header", lambda _path: header)
    original_unlink = Path.unlink
    generations = [first]

    def locked_unlink(path, *args, **kwargs):
        if path in generations:
            raise PermissionError("permanent Windows sharing violation")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    for index in range(1, 13):
        new_path = cache_root / f"cn_test_cloth_generation_{index:02d}.pc2"
        new_path.write_bytes(f"generation {index}".encode())
        plan = module.RunPlan(
            SimpleNamespace(), SimpleNamespace(), ((0, 0, 0),),
            ((1, 0, 0, 0), (0, 1, 0, 0),
             (0, 0, 1, 0), (0, 0, 0, 1)),
            obj.name, tmp_path, new_path, 1)
        module._attach_playback(plan, header)
        generations.append(new_path)

    assert modifier.filepath == str(generations[-1])
    assert list(obj.modifiers) == [modifier]
    assert not any(path.exists() for path in generations[:-1])
    assert generations[-1].is_file()
    assert len(module.pending_cleanup_paths()) <= 128


def test_generation_cleanup_never_deletes_lookalike_artist_path(
        blender_env, tmp_path):
    module = blender_env.solver_test
    cache_root = tmp_path / "owned-cache"
    foreign_root = tmp_path / "artist-cache"
    cache_root.mkdir()
    foreign_root.mkdir()
    new_path = cache_root / "cn_test_cloth_generation_b.pc2"
    foreign = foreign_root / "cn_test_cloth_artist_original.pc2"
    foreign.write_bytes(b"artist data")
    record = SimpleNamespace(
        obj=SimpleNamespace(name="Artist Cloth", modifiers=[]), extras=(),
        previous_paths={foreign}, new_path=new_path)

    module._commit_playback_cleanup((record,))

    assert foreign.read_bytes() == b"artist data"


def test_playback_reader_is_released_before_obsolete_pc2_cleanup(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="Reader Cloth", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    old_path = tmp_path / "cn_test_cloth_old.pc2"
    new_path = tmp_path / "cn_test_cloth_new.pc2"
    old_path.write_bytes(b"old")
    new_path.write_bytes(b"new")
    old_modifier = obj.modifiers.new(
        module.import_result.MODIFIER_NAME, "MESH_CACHE")
    old_modifier.filepath = str(old_path)
    module.mark_owned_playback(obj, old_modifier, old_modifier.filepath)
    released = []
    monkeypatch.setattr(
        blender_env.bpy.context, "view_layer",
        SimpleNamespace(update=lambda: released.append(True)), raising=False)
    calls = []

    def delete_after_release(path, **_kwargs):
        calls.append((path, bool(released)))
        return SimpleNamespace(success=True)

    monkeypatch.setattr(module, "_delete_cache_artifact",
                        delete_after_release)
    record = SimpleNamespace(
        obj=obj, extras=(old_modifier,), previous_paths={old_path},
        new_path=new_path)

    module._commit_playback_cleanup((record,))

    assert released == [True]
    assert calls and all(was_released for _path, was_released in calls)


def test_clear_tombstones_only_owned_unicode_cache_when_unlink_is_locked(
        blender_env, monkeypatch, tmp_path):
    module = blender_env.solver_test
    obj = blender_env.bpy.types.Object(name="披風 München", type="MESH")
    blender_env.bpy.data.objects[obj.name] = obj
    owned_path = tmp_path / "缓存 Ausgabe" / "cn_test_cloth_äöü.pc2"
    owned_path.parent.mkdir()
    owned_path.write_bytes(b"locked")
    owned = obj.modifiers.new(
        module.import_result.MODIFIER_NAME, "MESH_CACHE")
    owned.filepath = str(owned_path)
    module.mark_owned_playback(obj, owned, owned.filepath)
    artist = obj.modifiers.new("Artist Cache", "MESH_CACHE")
    artist.filepath = str(tmp_path / "artist.pc2")
    original_unlink = Path.unlink

    def locked_unlink(path, *args, **kwargs):
        if path == owned_path:
            raise PermissionError("cache is mapped by Blender")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    operator = module.CLOTHNEXT_OT_solver_test_clear()
    reports = []
    operator.report = lambda level, message: reports.append((level, message))

    assert operator.execute(SimpleNamespace(object=obj)) == {"FINISHED"}

    assert list(obj.modifiers) == [artist]
    assert artist.filepath.endswith("artist.pc2")
    assert not owned_path.exists()
    assert reports[-1][0] == {"INFO"}
    assert "nothing else was touched" in reports[-1][1]


def test_clear_from_active_collider_removes_scene_deformable_recovery_partial(
        blender_env, tmp_path):
    module = blender_env.solver_test
    cloth = blender_env.bpy.types.Object(name="Plane", type="MESH")
    collider = blender_env.bpy.types.Object(name="Retopo_Curve", type="MESH")
    blender_env.bpy.data.objects[cloth.name] = cloth
    blender_env.bpy.data.objects[collider.name] = collider
    partial = _stale_recovery_partial(tmp_path)
    modifier = cloth.modifiers.new(
        module.import_result.MODIFIER_NAME, "MESH_CACHE")
    modifier.filepath = str(partial)
    module.mark_owned_playback(cloth, modifier, modifier.filepath)
    operator = module.CLOTHNEXT_OT_solver_test_clear()
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    context = SimpleNamespace(
        object=collider, scene=SimpleNamespace(objects=(cloth, collider)))

    assert operator.execute(context) == {"FINISHED"}

    assert list(cloth.modifiers) == []
    assert not partial.exists()
    assert list(collider.modifiers) == []
    assert "Removed 1 Cloth NeXt test cache modifier(s)" in reports[-1][1]
    assert "1 cache file(s)" in reports[-1][1]


def test_attach_rolls_back_when_authoritative_ownership_commit_fails(
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

    with pytest.raises(RuntimeError, match="metadata boom"):
        module._attach_playback(plan, header)

    assert len(obj.modifiers) == 0


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


def test_cancel_clears_orphaned_active_controller_without_worker(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    module.shared_controller.transition(BakeState.PREPARING)
    module.shared_controller.transition(BakeState.STARTING_COMPANION)
    module._active_plan = None
    module._worker = None
    module._pending_job_id = ""
    cancelled = []
    monkeypatch.setattr(
        module.companion_manager, "cancel_startup",
        lambda job, reason="": cancelled.append((job, reason)))

    module.request_cancel()

    snapshot = module.shared_controller.snapshot()
    assert snapshot.state is BakeState.CANCELLED
    assert snapshot.active is False
    assert cancelled and cancelled[0][0] == snapshot.job_id
    module.shared_controller.reset()


def test_stale_ppf_pin_capture_cannot_abort_new_bake_job(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    current_job = module.shared_controller.transition(
        BakeState.PREPARING).job_id
    restored = []
    cleaned = []
    state = {
        "job_id": "old-ppf-job",
        "context": SimpleNamespace(scene=SimpleNamespace()),
        "collider_states": {"old": object()},
    }
    module._pin_capture = state
    module._pending_job_id = "old-ppf-job"
    monkeypatch.setattr(
        module, "_restore_pin_capture_state",
        lambda value: restored.append(value))
    monkeypatch.setattr(
        module, "_cleanup_collider_pump",
        lambda value: cleaned.append(value))

    assert module._pin_capture_pump() is None

    snapshot = module.shared_controller.snapshot()
    assert snapshot.job_id == current_job
    assert snapshot.state is BakeState.PREPARING
    assert module._pin_capture is None
    assert module._pending_job_id == ""
    assert restored == [state]
    assert cleaned == [state["collider_states"]]
    module.shared_controller.fail("test cleanup")
    module.shared_controller.reset()


def test_stale_ppf_startup_pump_cannot_fail_new_bake_job(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    current_job = module.shared_controller.transition(
        BakeState.PREPARING).job_id
    module.shared_controller.transition(BakeState.STARTING_COMPANION)
    module.shared_controller.transition(BakeState.WAITING_FOR_COMPANION)
    module._pending_plan = object()
    module._pending_job_id = "old-ppf-job"
    monkeypatch.setattr(
        module.companion_manager, "startup_status",
        lambda _job: (_ for _ in ()).throw(
            AssertionError("stale PPF startup queried Companion state")))

    assert module._startup_pump() is None

    snapshot = module.shared_controller.snapshot()
    assert snapshot.job_id == current_job
    assert snapshot.state is BakeState.WAITING_FOR_COMPANION
    assert module._pending_plan is None
    assert module._pending_job_id == ""
    module.shared_controller.fail("test cleanup")
    module.shared_controller.reset()


def test_periodic_recovery_event_refreshes_panel_from_durable_metadata(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    module.shared_controller.transition(BakeState.PREPARING)
    plan = SimpleNamespace()
    module._active_plan = plan
    module._worker = SimpleNamespace(is_alive=lambda: True)
    refreshed = []
    monkeypatch.setattr(
        module, "_refresh_recovery_ui", lambda value: refreshed.append(value))
    while not module._queue.empty():
        module._queue.get_nowait()
    module._queue.put(("event", SimpleNamespace(
        phase="RECOVERY_SAVED", message="Recovery checkpoint saved · Frame 5",
        frame_current=5, activity_code="RECOVERY_SAVED",
        process_id=None)))

    try:
        module._pump_once()

        assert refreshed == [plan]
        assert module.shared_controller.snapshot().status_message == (
            "Recovery checkpoint saved · Frame 5")
    finally:
        module._active_plan = None
        module._worker = None
        module.shared_controller.fail("test cleanup", "")
        module.shared_controller.reset()


def test_contact_build_error_publishes_overlay_without_cache_cleanup(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    from cloth_next.blender import intersection_overlay
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    for state in (BakeState.PREPARING, BakeState.STARTING_RUN,
                  BakeState.EXPORTING, BakeState.STARTING_SOLVER,
                  BakeState.UPLOADING, BakeState.BUILDING):
        module.shared_controller.transition(state)
    face = module.intersection_diagnostics.DegenerateFace(
        object_uuid="cloth", object_name="Cloth", role="CLOTH",
        combined_triangle_index=0, local_triangle_index=0,
        source_polygon_index=0, vertex_indices=(0, 1, 2),
        vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                  (2.0, 0.0, 0.0)))
    result = module.intersection_diagnostics.DiagnosticResult(
        degenerate_faces=(face,))
    plan = SimpleNamespace(solver_input=None)
    module._active_plan = plan
    module._worker = SimpleNamespace(is_alive=lambda: True)
    module._ram_auto_cancel_enabled = False
    published = []
    monkeypatch.setattr(intersection_overlay, "set_diagnostic_session",
                        lambda *args: published.append(args))
    monkeypatch.setattr(
        module, "_discard_incomplete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("contact validation touched playback cache")))
    while not module._queue.empty():
        module._queue.get_nowait()
    module._queue.put((
        "contact_error", "Initial self intersections found", "details",
        "CNX-E118", result))

    assert module._pump_once() is None

    assert published == [(result, None)]
    assert module.diagnostic_result() is result
    assert module.shared_controller.snapshot().state is BakeState.ERROR
    assert module._active_plan is None
    assert module._worker is None
    module.shared_controller.reset()


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


def test_recovery_resume_disabled_reason_never_reports_compatible(blender_env):
    module = blender_env.solver_test
    operator = module.CLOTHNEXT_OT_recovery_resume_latest

    assert operator._disabled_reason(SimpleNamespace(
        status_detail="Compatible")) == (
            "No verified resumable checkpoint is available")
    assert operator._disabled_reason(SimpleNamespace(
        status_detail="Recovery project state is Checkpoint Confirmed")) == (
            "Recovery project state is Checkpoint Confirmed")


def _recovery_settings(**changes):
    values = dict(
        enabled=True, resume_requested=False, keep_saved_states=3,
        auto_save=False, checkpoint_interval=20, save_on_cancel=True,
        save_on_finish=False,
        status="", status_detail="", compatible=False, resumable=False,
        latest_checkpoint_frame=0, checkpoint_count=0,
        older_checkpoint_preserved=False, recovery_directory="")
    values.update(changes)
    return SimpleNamespace(**values)


def _topology_fingerprint():
    return hashlib.sha256(json.dumps(
        sorted([("target-a", "topology")]),
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _verified_recovery(tmp_path, *, geometry="geometry"):
    """Durable RESUMABLE project whose checkpoint is authenticated on disk."""
    identity = recovery.RecoveryIdentity(
        scene_key="scene", param_key="param", export_uuids=("target-a",),
        geometry_fingerprint=geometry,
        topology_fingerprint=_topology_fingerprint(),
        frame_start=1, frame_end=180, fps=24.0, collider_sampling=(),
        solver_version="0.1.0", protocol_version="0.11",
        solver_schema_version="1", solver_installation_id="unregistered")
    project_root = tmp_path / "server" / "project"
    output = project_root / "session" / "output"
    output.mkdir(parents=True, exist_ok=True)
    metadata = recovery.metadata_path(tmp_path / "cache", "scene")
    record = recovery.create_project(
        metadata, project_id="project", identity=identity,
        server_data_root=tmp_path / "server", project_root=project_root)
    record = recovery.transition(
        metadata, record, recovery.ProjectState.RUNNING)
    (output / "state_20.bin.gz").write_bytes(gzip.compress(b"state-20"))
    record = recovery.confirm_saved_states(metadata, record, (20,), keep=10)
    recovery.transition(metadata, record, recovery.ProjectState.RESUMABLE)
    return metadata, identity


def _recovery_plan(tmp_path, *, geometry="geometry"):
    from cloth_next.ppf_run.session import SessionScene
    resolved = SimpleNamespace(
        package_version="0.1.0", protocol_version="0.11",
        schema_version="1", installation_id="unregistered",
        installation=None)
    scene = SessionScene(
        project_name="project", cloth_name="cloth", cloth_uuid="target-a",
        cloth_vertex_count=1, collider_name="", collider_uuid="",
        frame_count=1, data_payload=b"", param_payload=b"",
        data_hash="", param_hash="param")
    return dict(
        scene=scene, resolved=resolved,
        initial_local=((0.0, 0.0, 0.0),),
        world_matrix=((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0),
                      (0, 0, 0, 1)),
        cloth_object_name="cloth", work_directory=tmp_path / "work",
        pc2_path=tmp_path / "cache" / "cn_test_cloth.pc2",
        frame_count=1, frame_start=1, frame_end=180, fps=24.0,
        scene_cache_key="scene", param_cache_key="param",
        geometry_fingerprint=geometry, topology_signature="topology")


def test_load_post_refreshes_recovery_snapshot_from_disk(blender_env, tmp_path):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    settings = _recovery_settings(recovery_directory=str(metadata.parent))
    blender_env.bpy.context.scene = SimpleNamespace(
        cloth_next_recovery=settings)

    module._refresh_recovery_ui_from_disk()

    # No RunPlan or identity exists after a file load; the snapshot reports the
    # on-disk truth provisionally and Bake start re-verifies compatibility.
    assert settings.compatible is False
    assert settings.resumable is True
    assert settings.status == "Checkpoint Found"
    assert "Compatibility will be checked" in settings.status_detail
    assert settings.latest_checkpoint_frame == 20
    assert settings.checkpoint_count == 1


def test_refresh_recovery_ui_from_disk_without_metadata_is_not_resumable(
        blender_env, tmp_path):
    module = blender_env.solver_test
    settings = _recovery_settings(
        recovery_directory=str(tmp_path / "empty"))
    blender_env.bpy.context.scene = SimpleNamespace(
        cloth_next_recovery=settings)

    module._refresh_recovery_ui_from_disk()

    assert settings.resumable is False
    assert settings.compatible is False
    assert settings.status == "No Recovery Checkpoint"
    assert settings.latest_checkpoint_frame == 0
    assert settings.checkpoint_count == 0


def test_disk_refresh_marks_corrupt_checkpoint_invalid(blender_env, tmp_path):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    record = recovery.load_project(metadata)
    Path(record.checkpoints[-1].checkpoint_path).write_bytes(b"truncated")
    settings = _recovery_settings(recovery_directory=str(metadata.parent))
    blender_env.bpy.context.scene = SimpleNamespace(
        name="Recovery Scene", cloth_next_recovery=settings, objects=())

    module._refresh_recovery_ui_from_disk()

    assert settings.status == "Recovery Metadata Invalid"
    assert settings.resumable is False
    assert settings.checkpoint_count == 0


def test_load_post_discovers_new_recovery_root_from_enabled_cloth_cache(
        blender_env, tmp_path, monkeypatch):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    stale = tmp_path / "stale" / "old-scene"
    settings = _recovery_settings(recovery_directory=str(stale))
    cloth = SimpleNamespace(cloth_next=SimpleNamespace(
        enabled=True, role="CLOTH",
        persistent_export_id="cloth-persistent-id",
        cache_directory=str(tmp_path / "cache")))
    collider = SimpleNamespace(cloth_next=SimpleNamespace(
        enabled=True, role="COLLIDER",
        persistent_export_id="collider-persistent-id",
        cache_directory=str(tmp_path / "unrelated")))
    monkeypatch.setattr(
        module.export_identity, "export_uuid_from_identity",
        lambda _identity, role: "target-a" if role == "CLOTH" else "collider")
    # The fixture project contains one cloth and no collider; exclude the
    # unrelated collider from the enabled Recovery identity.
    collider.cloth_next.enabled = False
    blender_env.bpy.context.scene = SimpleNamespace(
        cloth_next_recovery=settings, objects=(cloth, collider))

    module._refresh_recovery_ui_from_disk()

    assert settings.recovery_directory == str(metadata.parent)
    assert settings.resumable is True
    assert settings.status == "Checkpoint Found"
    assert settings.checkpoint_count == 1


def test_recovery_scan_refuses_ambiguous_projects_for_same_objects(
        blender_env, tmp_path, monkeypatch):
    module = blender_env.solver_test
    first_metadata, first_identity = _verified_recovery(tmp_path)
    second_identity = replace(first_identity, scene_key="scene-two")
    second_root = tmp_path / "server" / "project-two"
    output = second_root / "session" / "output"
    output.mkdir(parents=True)
    second_metadata = recovery.metadata_path(tmp_path / "cache", "scene-two")
    record = recovery.create_project(
        second_metadata, project_id="project-two", identity=second_identity,
        server_data_root=tmp_path / "server", project_root=second_root)
    record = recovery.transition(
        second_metadata, record, recovery.ProjectState.RUNNING)
    (output / "state_20.bin.gz").write_bytes(gzip.compress(b"state-two"))
    record = recovery.confirm_saved_states(
        second_metadata, record, (20,), keep=3)
    recovery.transition(
        second_metadata, record, recovery.ProjectState.RESUMABLE)
    settings = _recovery_settings()
    cloth = SimpleNamespace(name="Cloth", cloth_next=SimpleNamespace(
        enabled=True, role="CLOTH", persistent_export_id="cloth-id",
        cache_directory=str(tmp_path / "cache")))
    blender_env.bpy.context.scene = SimpleNamespace(
        name="Scene", cloth_next_recovery=settings, objects=(cloth,))
    monkeypatch.setattr(
        module.export_identity, "export_uuid_from_identity",
        lambda _identity, _role: "target-a")

    diagnostics = module._refresh_recovery_ui_from_disk()

    assert first_metadata in tuple(map(type(first_metadata),
                                      diagnostics["candidate_metadata_paths"]))
    assert settings.status == "Recovery Check Failed"
    assert settings.resumable is False
    assert settings.recovery_directory == ""


def test_recovery_scan_ignores_newer_unrelated_project(
        blender_env, tmp_path, monkeypatch):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    unrelated_identity = replace(_identity, scene_key="unrelated",
                                 export_uuids=("other-object",))
    unrelated_root = tmp_path / "server" / "unrelated"
    unrelated_output = unrelated_root / "session" / "output"
    unrelated_output.mkdir(parents=True)
    unrelated_metadata = recovery.metadata_path(
        tmp_path / "cache", "unrelated")
    record = recovery.create_project(
        unrelated_metadata, project_id="unrelated",
        identity=unrelated_identity, server_data_root=tmp_path / "server",
        project_root=unrelated_root)
    record = recovery.transition(
        unrelated_metadata, record, recovery.ProjectState.RUNNING)
    (unrelated_output / "state_99.bin.gz").write_bytes(
        gzip.compress(b"unrelated"))
    record = recovery.confirm_saved_states(
        unrelated_metadata, record, (99,), keep=3)
    recovery.transition(
        unrelated_metadata, record, recovery.ProjectState.RESUMABLE)
    settings = _recovery_settings()
    cloth = SimpleNamespace(name="Cloth", cloth_next=SimpleNamespace(
        enabled=True, role="CLOTH", persistent_export_id="cloth-id",
        cache_directory=str(tmp_path / "cache")))
    blender_env.bpy.context.scene = SimpleNamespace(
        name="Scene", cloth_next_recovery=settings, objects=(cloth,))
    monkeypatch.setattr(
        module.export_identity, "export_uuid_from_identity",
        lambda _identity, _role: "target-a")

    module._refresh_recovery_ui_from_disk()

    assert settings.recovery_directory == str(metadata.parent)
    assert settings.latest_checkpoint_frame == 20
    assert settings.status == "Checkpoint Found"


def test_refresh_recovery_ui_from_disk_without_directory_is_untouched(
        blender_env):
    module = blender_env.solver_test
    settings = _recovery_settings()
    blender_env.bpy.context.scene = SimpleNamespace(
        cloth_next_recovery=settings)

    module._refresh_recovery_ui_from_disk()

    assert settings.resumable is False
    assert settings.status == "No Recovery Checkpoint"


def test_load_post_recovery_handler_registered_exactly_once(blender_env):
    module = blender_env.solver_test
    container = blender_env.bpy.app.handlers.load_post

    module.install_recovery_ui_handler()
    module.install_recovery_ui_handler()

    assert container.count(module._on_load_post_refresh_recovery) == 1


def test_load_pre_overlay_reset_handler_registered_once_and_clears_runtime(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    container = blender_env.bpy.app.handlers.load_pre
    from cloth_next.blender import intersection_overlay
    resets = []
    monkeypatch.setattr(intersection_overlay, "reset_runtime",
                        lambda: resets.append(True))

    module.install_recovery_ui_handler()
    module.install_recovery_ui_handler()
    module._on_load_pre_reset_overlay(None)

    assert container.count(module._on_load_pre_reset_overlay) == 1
    assert hasattr(module._on_load_pre_reset_overlay, "_bpy_persistent")
    assert resets == [True]


def test_recovery_load_handler_is_blender_persistent(blender_env):
    module = blender_env.solver_test

    assert hasattr(module._on_load_post_refresh_recovery, "_bpy_persistent")


def test_load_post_defers_authoritative_recovery_verification(
        blender_env, tmp_path):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    settings = _recovery_settings(recovery_directory=str(metadata.parent))
    blender_env.bpy.context.scene = SimpleNamespace(
        name="Recovery Scene", cloth_next_recovery=settings, objects=())

    module._on_load_post_refresh_recovery(None)

    assert settings.status == "Checking for Recovery"
    assert blender_env.bpy.app.timers.is_registered(
        module._delayed_recovery_refresh)
    assert module._delayed_recovery_refresh() is None
    assert settings.status == "Checkpoint Found"
    assert settings.resumable is True


def test_uninstall_removes_recovery_handler(blender_env):
    module = blender_env.solver_test
    container = blender_env.bpy.app.handlers.load_post
    module.install_recovery_ui_handler()

    module.uninstall_recovery_ui_handler()

    assert module._on_load_post_refresh_recovery not in container
    assert module._on_load_pre_reset_overlay not in (
        blender_env.bpy.app.handlers.load_pre)
    assert not blender_env.bpy.app.timers.is_registered(
        module._delayed_recovery_refresh)


def test_install_purges_stale_recovery_handlers(blender_env):
    module = blender_env.solver_test
    container = blender_env.bpy.app.handlers.load_post
    stale = lambda *_args: None  # noqa: E731 - old module instance callback
    stale._clothnext_recovery_handler = True
    container.append(stale)

    module.install_recovery_ui_handler()

    assert stale not in container
    assert module._on_load_post_refresh_recovery in container


def test_recovery_resume_poll_requires_resumable_and_idle(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    operator = module.CLOTHNEXT_OT_recovery_resume_latest
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    settings = _recovery_settings(resumable=True, status_detail="Verified")
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))

    monkeypatch.setattr(module, "run_active", lambda: False)
    assert operator.poll(context) is True

    settings.resumable = False
    assert operator.poll(context) is False


def test_recovery_resume_operator_starts_production_service(
        blender_env, tmp_path, monkeypatch):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    settings = _recovery_settings(
        resumable=True, recovery_directory=str(metadata.parent))
    context = SimpleNamespace(scene=SimpleNamespace(
        name="Recovery Scene", cloth_next_recovery=settings, objects=()))
    blender_env.bpy.context.scene = context.scene
    operator = module.CLOTHNEXT_OT_recovery_resume_latest()
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()

    def begin(_context):
        job_id = module.shared_controller.transition(
            BakeState.PREPARING, status_message="Validating Resume").job_id
        return job_id, True

    monkeypatch.setattr(module, "run_active", lambda: False)
    monkeypatch.setattr(module, "begin_production_bake", begin)

    assert operator.execute(context) == {"FINISHED"}
    assert module.shared_controller.snapshot().state is BakeState.PREPARING
    assert reports[-1][0] == {"INFO"}
    module.shared_controller.fail("test cleanup")
    module.shared_controller.reset()


def test_recovery_resume_operator_cancels_when_production_start_fails(
        blender_env, tmp_path, monkeypatch):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    settings = _recovery_settings(
        resumable=True, recovery_directory=str(metadata.parent))
    context = SimpleNamespace(scene=SimpleNamespace(
        name="Recovery Scene", cloth_next_recovery=settings, objects=()))
    blender_env.bpy.context.scene = context.scene
    operator = module.CLOTHNEXT_OT_recovery_resume_latest()
    reports = []
    operator.report = lambda level, message: reports.append((level, message))
    if module.shared_controller.snapshot().state is not BakeState.IDLE:
        module.shared_controller.reset()
    monkeypatch.setattr(module, "run_active", lambda: False)
    monkeypatch.setattr(
        module, "begin_production_bake",
        lambda _context: (_ for _ in ()).throw(
            module.SceneValidationError("Companion startup cancelled")))

    assert operator.execute(context) == {"CANCELLED"}
    assert settings.resume_requested is False
    assert settings.status == "Recovery Check Failed"
    assert "Companion startup cancelled" in settings.status_detail
    assert reports[-1][0] == {"ERROR"}

    settings.resumable = True
    monkeypatch.setattr(module, "run_active", lambda: True)
    assert operator.poll(context) is False

    monkeypatch.setattr(module, "run_active", lambda: False)
    monkeypatch.setattr(module.shared_controller, "snapshot",
                        lambda: SimpleNamespace(active=True))
    assert operator.poll(context) is False


def test_configure_recovery_resumes_compatible_project(blender_env, tmp_path):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    plan = module.RunPlan(**_recovery_plan(tmp_path))
    settings = _recovery_settings(
        resume_requested=True, recovery_directory=str(metadata.parent),
        auto_save=True, checkpoint_interval=2)
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))
    snapshot = SimpleNamespace(collider_objs=())

    result = module._configure_recovery(context, snapshot, plan)

    assert result.recovery_options is not None
    assert result.recovery_options.resume is True
    assert result.recovery_options.metadata_path == metadata
    assert result.recovery_options.auto_save_interval == 2
    assert result.recovery_options.keep_saved_states == 3
    assert settings.recovery_directory == str(metadata.parent)
    assert settings.resumable is True
    assert settings.compatible is True
    assert settings.resume_requested is False
    assert settings.status == "Resume Available"


def test_configure_recovery_uses_selected_project_when_export_key_changes(
        blender_env, tmp_path):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    plan = module.RunPlan(**{
        **_recovery_plan(tmp_path), "scene_cache_key": "runtime-key-after-reopen"})
    settings = _recovery_settings(
        resume_requested=True, recovery_directory=str(metadata.parent))
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))

    result = module._configure_recovery(
        context, SimpleNamespace(collider_objs=()), plan)

    assert result.recovery_options.resume is True
    assert result.recovery_options.metadata_path == metadata
    assert result.recovery_options.identity.scene_key == "scene"
    assert result.scene.project_name == "project"


def test_recovery_identity_uses_wire_param_hash_not_unstable_cache_recipe(
        blender_env, tmp_path):
    """DO NOT REGRESS: a cache-key change must not invalidate Resume.

    Only the canonical PARAM bytes accepted by the solver define parameter
    compatibility. Internal cache recipes are implementation details and may
    change across an otherwise identical second Bake preparation.
    """
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    plan = module.RunPlan(**{
        **_recovery_plan(tmp_path),
        # Empty is valid: Recovery must not be gated on this optional recipe.
        "param_cache_key": "",
    })
    settings = _recovery_settings(
        resume_requested=True, recovery_directory=str(metadata.parent))
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))

    result = module._configure_recovery(
        context, SimpleNamespace(collider_objs=()), plan)

    assert result.recovery_options.resume is True
    assert result.recovery_options.identity.param_key == "param"


def test_recovery_uses_wire_scene_hash_when_export_cache_is_unsafe(
        blender_env, tmp_path):
    """Recovery remains durable when the optional export cache is declined."""
    module = blender_env.solver_test
    plan = module.RunPlan(**{
        **_recovery_plan(tmp_path),
        "scene_cache_key": "",
        "scene": replace(
            _recovery_plan(tmp_path)["scene"], data_hash="wire-scene"),
    })
    settings = _recovery_settings(auto_save=True, checkpoint_interval=5)
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))

    result = module._configure_recovery(
        context, SimpleNamespace(collider_objs=()), plan)

    assert result.recovery_options is not None
    assert result.recovery_options.identity.scene_key == "wire-scene"
    assert result.recovery_options.auto_save_interval == 5
    assert settings.recovery_directory.endswith("wire-scene")


def test_configure_recovery_missing_selected_metadata_never_starts_fresh(
        blender_env, tmp_path):
    module = blender_env.solver_test
    plan = module.RunPlan(**_recovery_plan(tmp_path))
    settings = _recovery_settings(
        resume_requested=True,
        recovery_directory=str(tmp_path / "missing-project"))
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))

    with pytest.raises(module.SceneValidationError) as caught:
        module._configure_recovery(
            context, SimpleNamespace(collider_objs=()), plan)

    assert "without starting a new Bake" in str(caught.value)
    assert settings.resume_requested is False


def test_configure_recovery_refuses_incompatible_resume(blender_env, tmp_path):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    plan = module.RunPlan(**{
        **_recovery_plan(tmp_path), "geometry_fingerprint": "changed-geometry"})
    settings = _recovery_settings(
        resume_requested=True, recovery_directory=str(metadata.parent))
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))
    snapshot = SimpleNamespace(collider_objs=())

    with pytest.raises(module.SceneValidationError) as caught:
        module._configure_recovery(context, snapshot, plan)

    assert "cannot be resumed" in str(caught.value)
    assert "Geometry changed" in str(caught.value)
    # The refusal is not self-perpetuating and the panel stops claiming a
    # compatible resume is available.
    assert settings.resume_requested is False
    assert settings.resumable is False
    assert settings.compatible is False
    assert "Geometry changed" in settings.status_detail


def test_configure_recovery_refuses_when_checkpoint_vanish_race(blender_env,
                                                                tmp_path,
                                                                monkeypatch):
    module = blender_env.solver_test
    metadata, _identity = _verified_recovery(tmp_path)
    plan = module.RunPlan(**_recovery_plan(tmp_path))
    settings = _recovery_settings(
        resume_requested=True, recovery_directory=str(metadata.parent))
    context = SimpleNamespace(scene=SimpleNamespace(
        cloth_next_recovery=settings))
    snapshot = SimpleNamespace(collider_objs=())
    denied = recovery.replace(
        recovery.evaluate_resumable(metadata),
        available=False, resumable=False,
        reason="No verified resumable checkpoint is available")
    monkeypatch.setattr(module.recovery, "reconcile_resumable",
                        lambda *_args, **_kwargs: (denied, False))

    with pytest.raises(module.SceneValidationError) as caught:
        module._configure_recovery(context, snapshot, plan)

    assert "can no longer be resumed" in str(caught.value)
    assert settings.resume_requested is False
    assert settings.resumable is False


def test_degenerate_triangle_vertices_are_selected_deterministically(
        blender_env):
    module = blender_env.solver_test
    triangles = ((4, 2, 7), (7, 9, 4), (100, 101, 102))

    assert module._vertices_for_triangles(triangles, (1, 0, 1)) == (
        2, 4, 7, 9)


def test_published_degenerate_diagnostics_retain_exact_solver_input(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    from cloth_next.blender import intersection_overlay
    captured = []
    obj = SimpleNamespace(name="Cloth")
    vertices = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                (2.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    triangles = ((0, 1, 2), (0, 2, 3))
    identity = tuple(tuple(1.0 if row == column else 0.0
                           for column in range(4)) for row in range(4))
    monkeypatch.setattr(module, "_source_polygon_indices",
                        lambda *_args: (4, 9))
    monkeypatch.setattr(module.export_identity, "export_uuid",
                        lambda _obj: "cloth-uuid")
    monkeypatch.setattr(intersection_overlay, "set_diagnostic_session",
                        captured.append)

    module._publish_degenerate_diagnostics(
        obj, "CLOTH", vertices, triangles, (0,), identity, 7)

    result = module.diagnostic_result()
    assert captured == [result]
    assert result.snapshot.bake_start_frame == 7
    assert len(result.snapshot.triangles) == 2
    assert result.snapshot.triangles[0].input_vertices == vertices[:3]
    assert result.degenerate_faces[0].vertex_indices == (0, 1, 2)
    assert result.degenerate_faces[0].source_polygon_index == 4
    module._clear_intersection_diagnostics()


def test_local_geometry_gate_publishes_combined_result_before_solver_start(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    diagnostics = module.intersection_diagnostics
    snapshot = diagnostics.SolverInputSnapshot(1, ())
    face = diagnostics.DegenerateFace(
        "a", "Top", "CLOTH", 0, 0, 4, (0, 1, 2),
        ((0.0, 0.0, 0.0),) * 3)
    result = diagnostics.DiagnosticResult(
        snapshot=snapshot,
        violations=(diagnostics.IntersectionViolation(
            "SELF_INTERSECTION", "STRICT_CROSSING", (), (1, 2), 1),),
        detected_count=1, degenerate_faces=(face,))
    stats = diagnostics.LocalDiagnosticStats(3, 2, 1)
    published = []
    monkeypatch.setattr(module, "_combined_degenerate_indices",
                        lambda *_args: (0,))
    monkeypatch.setattr(module, "_local_geometry_diagnostics",
                        lambda *_args, **_kwargs: (result, stats))
    monkeypatch.setattr(module, "_publish_local_geometry_diagnostics",
                        lambda value, value_stats: published.append(
                            (value, value_stats)))

    with pytest.raises(module.SceneValidationError, match=(
            "1 degenerate face.*1 intersection")):
        module._validate_local_solver_geometry(snapshot, ())

    assert published == [(result, stats)]


def test_clean_local_geometry_gate_allows_normal_plan_construction(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    diagnostics = module.intersection_diagnostics
    snapshot = diagnostics.SolverInputSnapshot(1, ())
    clean = diagnostics.DiagnosticResult(snapshot=snapshot)
    stats = diagnostics.LocalDiagnosticStats()
    monkeypatch.setattr(module, "_combined_degenerate_indices",
                        lambda *_args: ())
    monkeypatch.setattr(module, "_local_geometry_diagnostics",
                        lambda *_args, **_kwargs: (clean, stats))
    monkeypatch.setattr(
        module, "_publish_local_geometry_diagnostics",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("clean geometry was published as an issue")))

    assert module._validate_local_solver_geometry(snapshot, ()) == (
        clean, stats)


def test_local_revalidation_replaces_pre_weld_snapshot_ids(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    diagnostics = module.intersection_diagnostics
    old_snapshot = diagnostics.SolverInputSnapshot(1, ())
    fresh_snapshot = diagnostics.SolverInputSnapshot(2, ())
    fresh = diagnostics.DiagnosticResult(snapshot=fresh_snapshot)
    stats = diagnostics.LocalDiagnosticStats()
    published = []
    monkeypatch.setattr(module, "validate_scene", lambda _context: object())
    monkeypatch.setattr(module, "_build_local_geometry_snapshot",
                        lambda *_args: (fresh_snapshot, ()))
    monkeypatch.setattr(module, "_combined_degenerate_indices",
                        lambda *_args: ())
    monkeypatch.setattr(module, "_local_geometry_diagnostics",
                        lambda *_args, **_kwargs: (fresh, stats))
    monkeypatch.setattr(module, "_publish_local_geometry_diagnostics",
                        lambda result, result_stats: published.append(
                            (result, result_stats)))

    result, returned_stats = module._revalidate_local_geometry(object())

    assert result.snapshot is fresh_snapshot
    assert result.snapshot is not old_snapshot
    assert returned_stats is stats
    assert published == [(fresh, stats)]


def test_clean_auto_fix_recheck_automatically_continues_contact_validation(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    context = object()
    clean = module.intersection_diagnostics.DiagnosticResult()
    continued = []
    monkeypatch.setattr(module, "_continue_contact_validation",
                        lambda value: continued.append(value))

    message = module._continue_after_auto_fix_recheck(context, clean)

    assert continued == [context]
    assert "continues without frame simulation" in message


def test_degenerate_auto_fix_recheck_never_starts_solver_contact_validation(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    face = module.intersection_diagnostics.DegenerateFace(
        object_uuid="cloth", object_name="Cloth", role="CLOTH",
        combined_triangle_index=0, local_triangle_index=0,
        source_polygon_index=0, vertex_indices=(0, 1, 2),
        vertices=((0.0, 0.0, 0.0),) * 3)
    blocked = module.intersection_diagnostics.DiagnosticResult(
        degenerate_faces=(face,))
    monkeypatch.setattr(
        module, "_continue_contact_validation",
        lambda _context: (_ for _ in ()).throw(
            AssertionError("degenerate geometry reached solver")))

    message = module._continue_after_auto_fix_recheck(object(), blocked)

    assert "contact validation was not started" in message


def test_auto_fix_object_skips_scene_objects_without_export_identity(
        blender_env):
    module = blender_env.solver_test
    target = SimpleNamespace(cloth_next=SimpleNamespace(
        persistent_export_id="cloth-id", role="CLOTH"))
    camera = SimpleNamespace()
    light = SimpleNamespace(cloth_next=SimpleNamespace(
        persistent_export_id="", role=""))
    expected = module.export_identity.export_uuid_from_identity(
        "cloth-id", "CLOTH")

    resolved = module._auto_fix_object(
        SimpleNamespace(objects=(camera, light, target)), expected)

    assert resolved is target


def test_auto_fix_leaves_preflight_edit_mode_before_snapshot_validation(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    active = SimpleNamespace(name="Cloth", mode="EDIT")
    calls = []

    def mode_set(*, mode):
        calls.append(mode)
        active.mode = mode

    monkeypatch.setattr(
        module.bpy.ops, "object", SimpleNamespace(mode_set=mode_set),
        raising=False)

    module._leave_edit_mode_for_auto_fix(SimpleNamespace(object=active))

    assert calls == ["OBJECT"]
    assert active.mode == "OBJECT"


def test_auto_fix_operator_repairs_intersection_and_all_safe_degenerates(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    diagnostics = module.intersection_diagnostics
    from cloth_next.blender import intersection_overlay

    first_vertices = ((-1.0, -1.0, 0.0), (1.0, -1.0, 0.0),
                      (0.0, 1.0, 0.0))
    second_vertices = ((0.0, -0.5, -0.001), (0.0, 0.5, 0.001),
                       (0.0, 0.5, -0.001))
    elements = tuple(
        diagnostics.IntersectionElement(
            kind="TRIANGLE", object_uuid="cloth", object_name="Cloth",
            role="CLOTH", combined_triangle_index=index,
            local_triangle_index=index, source_polygon_index=index,
            vertices=vertices)
        for index, vertices in enumerate((first_vertices, second_vertices)))
    violation = diagnostics.IntersectionViolation(
        classification="SELF_INTERSECTION", detection_method="SOLVER_REPORTED",
        elements=elements, combined_pair=(0, 1), total_count=1)
    degenerates = tuple(
        diagnostics.DegenerateFace(
            object_uuid="cloth", object_name="Cloth", role="CLOTH",
            combined_triangle_index=index + 2,
            local_triangle_index=index + 2,
            source_polygon_index=index + 2,
            vertex_indices=indices,
            vertices=((0.0, float(index), 0.0),
                      (1.0, float(index), 0.0),
                      (2.0, float(index), 0.0)))
        for index, indices in enumerate(((6, 7, 8), (9, 10, 11))))
    result = diagnostics.DiagnosticResult(
        snapshot=object(), violations=(violation,), detected_count=1,
        degenerate_faces=degenerates)
    module._diagnostic_result = result
    module._intersection_violations = (violation,)

    obj = blender_env.bpy.types.Object(name="Cloth", type="MESH")
    obj.cloth_next = SimpleNamespace(collision=SimpleNamespace(
        collision_gap=0.01, surface_offset=0.0))
    updates = []
    obj.data = SimpleNamespace(
        vertices=[SimpleNamespace(co=np.zeros(3)) for _index in range(12)],
        update=lambda: updates.append(True))
    foreign = blender_env.bpy.types.Object(name="Foreign", type="MESH")
    foreign.data = SimpleNamespace(
        vertices=[SimpleNamespace(co=np.zeros(3)) for _index in range(3)])

    class IdentityLinear:
        def __matmul__(self, value):
            return np.asarray(value, dtype=float)

    pairs = (((tuple(("cloth", index) for index in (0, 1, 2))),
              first_vertices,
              tuple(("cloth", index) for index in (3, 4, 5)),
              second_vertices),)
    monkeypatch.setattr(
        module, "_auto_fix_snapshot_pairs",
        lambda *_args: (pairs, {
            "cloth": (obj, IdentityLinear(), lambda value: value)}))
    forgotten = []
    monkeypatch.setattr(module.validation_state, "forget", forgotten.append)
    monkeypatch.setattr(intersection_overlay, "solver_input_snapshot",
                        lambda: result.snapshot)
    monkeypatch.setattr(
        module.bpy.ops.clothnext, "bake",
        lambda *_args, **_kwargs:
            (_ for _ in ()).throw(AssertionError("Auto Fix started a Bake")),
        raising=False)
    reports = []
    operator = module.CLOTHNEXT_OT_intersection_auto_fix()
    operator.report = lambda level, message: reports.append((level, message))

    progress = []
    window_manager = SimpleNamespace(
        progress_begin=lambda minimum, maximum:
            progress.append(("begin", minimum, maximum)),
        progress_update=lambda value: progress.append(("update", value)),
        progress_end=lambda: progress.append(("end",)))
    outcome = operator.execute(SimpleNamespace(
        scene=SimpleNamespace(), window_manager=window_manager))

    assert outcome == {"FINISHED"}
    assert updates == [True]
    assert forgotten == [obj]
    assert any(np.linalg.norm(vertex.co) > 0.0
               for vertex in obj.data.vertices)
    assert all(np.allclose(vertex.co, 0.0)
               for vertex in foreign.data.vertices)
    assert module.diagnostic_result() == diagnostics.DiagnosticResult()
    assert intersection_overlay.presentation_diagnostics() == ()
    assert progress[0] == ("begin", 0, 100)
    assert progress[-1] == ("end",)
    assert any(item[0] == "update" and item[1] == 100 for item in progress)
    assert "1 intersection(s) and 2 degenerate face(s) repaired" in reports[-1][1]


def test_auto_fix_applies_degenerate_when_real_intersection_is_unsafe(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    diagnostics = module.intersection_diagnostics
    from cloth_next.blender import intersection_overlay

    first_vertices = (
        (-0.13171677261363257, 0.8933217002075959, 0.03316186803011829),
        (-0.1329977632750623, 0.8900272055884498, 0.03536378793909536),
        (-0.12930788272870863, 0.8928136436714242, 0.03486502366694917),
    )
    second_vertices = (
        (-0.13284317647572763, 0.8895712118935805, 0.035087394070013066),
        (-0.13187831742111022, 0.8932520591380577, 0.033785075157261944),
        (-0.12909523623423347, 0.8923811485466382, 0.035036471461651224),
    )
    elements = tuple(
        diagnostics.IntersectionElement(
            kind="TRIANGLE", object_uuid="cloth", object_name="Cloth",
            role="CLOTH", combined_triangle_index=index,
            local_triangle_index=index, source_polygon_index=index,
            vertices=vertices)
        for index, vertices in enumerate((first_vertices, second_vertices)))
    violation = diagnostics.IntersectionViolation(
        classification="SELF_INTERSECTION", detection_method="STRICT_CROSSING",
        elements=elements, combined_pair=(0, 1), total_count=1)
    face = diagnostics.DegenerateFace(
        object_uuid="cloth", object_name="Cloth", role="CLOTH",
        combined_triangle_index=2, local_triangle_index=2,
        source_polygon_index=2, vertex_indices=(6, 7, 8),
        vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                  (2.0, 0.0, 0.0)))
    result = diagnostics.DiagnosticResult(
        snapshot=object(), violations=(violation,), detected_count=1,
        degenerate_faces=(face,))
    module._diagnostic_result = result
    module._intersection_violations = (violation,)

    obj = blender_env.bpy.types.Object(name="Cloth", type="MESH")
    obj.cloth_next = SimpleNamespace(collision=SimpleNamespace(
        collision_gap=0.01, surface_offset=0.0))
    updates = []
    obj.data = SimpleNamespace(
        vertices=[SimpleNamespace(co=np.zeros(3)) for _index in range(9)],
        update=lambda: updates.append(True))

    class IdentityLinear:
        def __matmul__(self, value):
            return np.asarray(value, dtype=float)

    pairs = ((
        tuple(("cloth", index) for index in (0, 1, 2)), first_vertices,
        tuple(("cloth", index) for index in (3, 4, 5)), second_vertices),)
    monkeypatch.setattr(
        module, "_auto_fix_snapshot_pairs",
        lambda *_args: (pairs, {
            "cloth": (obj, IdentityLinear(), lambda value: value)}))
    monkeypatch.setattr(intersection_overlay, "solver_input_snapshot",
                        lambda: result.snapshot)
    monkeypatch.setattr(module.validation_state, "forget", lambda _obj: None)
    fresh_snapshot = object()
    remaining_result = diagnostics.DiagnosticResult(
        snapshot=fresh_snapshot, violations=(violation,), detected_count=1)

    def revalidate(_context):
        module._diagnostic_result = remaining_result
        module._intersection_violations = remaining_result.violations
        intersection_overlay.set_diagnostic_session(remaining_result)
        return remaining_result, diagnostics.LocalDiagnosticStats(2, 1, 1)

    monkeypatch.setattr(module, "_revalidate_local_geometry", revalidate)
    reports = []
    operator = module.CLOTHNEXT_OT_intersection_auto_fix()
    operator.report = lambda level, message: reports.append((level, message))

    outcome = operator.execute(SimpleNamespace(
        scene=SimpleNamespace(), window_manager=SimpleNamespace()))

    assert outcome == {"FINISHED"}
    assert updates == [True]
    assert np.linalg.norm(obj.data.vertices[7].co) > 0.0
    assert all(np.allclose(obj.data.vertices[index].co, 0.0)
               for index in (*range(6), 6, 8))
    assert "0 intersection(s) and 1 degenerate face(s) repaired" \
        in reports[-1][1]
    assert "1 skipped" in reports[-1][1]
    assert "Local recheck: 0 degenerate face(s), 1 intersection(s) remain" \
        in reports[-1][1]
    assert module.diagnostic_result().snapshot is fresh_snapshot
    assert module.diagnostic_result().snapshot is not result.snapshot
    assert intersection_overlay.presentation_diagnostics() == (violation,)
    module._clear_intersection_diagnostics()


def test_auto_fix_degenerate_requires_retained_solver_input(blender_env):
    module = blender_env.solver_test
    diagnostics = module.intersection_diagnostics
    face = diagnostics.DegenerateFace(
        object_uuid="cloth", object_name="Cloth", role="CLOTH",
        combined_triangle_index=0, local_triangle_index=0,
        source_polygon_index=0, vertex_indices=(0, 1, 2),
        vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0),
                  (2.0, 0.0, 0.0)))
    module._diagnostic_result = diagnostics.DiagnosticResult(
        degenerate_faces=(face,))
    reports = []
    operator = module.CLOTHNEXT_OT_intersection_auto_fix()
    operator.report = lambda level, message: reports.append((level, message))

    outcome = operator.execute(SimpleNamespace(
        scene=SimpleNamespace(frame_current=1)))

    assert outcome == {"CANCELLED"}
    assert "snapshot" in reports[-1][1]
    assert module.diagnostic_result().degenerate_faces == (face,)
    module._clear_intersection_diagnostics()


def test_bake_window_diagnostic_object_label_uses_affected_objects(
        blender_env):
    module = blender_env.solver_test
    element = SimpleNamespace(object_name="Shorts")
    result = SimpleNamespace(
        violations=(SimpleNamespace(elements=(element, element)),),
        degenerate_faces=())

    assert module._diagnostic_object_label(result) == "Shorts"
