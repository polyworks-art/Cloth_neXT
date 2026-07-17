# SPDX-License-Identifier: GPL-3.0-or-later
"""Production Solver-panel Bake entry point and shared run-service contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.bake.controller import shared_controller
from cloth_next.bake.status import BakeState


class RecordingLayout:
    def __init__(self, sink=None):
        self.sink = sink or self
        if sink is None:
            self.labels = []
            self.operators = []
        self.enabled = True
        self.scale_y = 1.0

    def label(self, text="", **_kw):
        self.sink.labels.append(text)

    def operator(self, identifier, text="", **_kw):
        self.sink.operators.append((identifier, text, self.enabled))
        return SimpleNamespace()

    def row(self, **_kw):
        return RecordingLayout(self.sink)

    def column(self, **_kw):
        return RecordingLayout(self.sink)


def _objects(env, cloth_count=1, collider_count=1):
    result = []
    for number in range(cloth_count):
        obj = env.bpy.types.Object(name=f"Cloth{number}", type="MESH")
        obj.cloth_next.enabled = True
        obj.cloth_next.role = "CLOTH"
        obj.animation_data = None
        result.append(obj)
    for number in range(collider_count):
        obj = env.bpy.types.Object(name=f"Collider{number}", type="MESH")
        obj.cloth_next.enabled = True
        obj.cloth_next.role = "COLLIDER"
        obj.animation_data = None
        result.append(obj)
    return result


def _context(env, objects, *, auto_launch=True):
    prefs = SimpleNamespace(auto_launch_bake_window=auto_launch,
                            telemetry_refresh_seconds=1.0,
                            external_solver_path="")
    return SimpleNamespace(
        object=objects[0] if objects else None,
        scene=SimpleNamespace(objects=objects, frame_start=1, frame_end=8),
        preferences=SimpleNamespace(addons={"cloth_next":
                                            SimpleNamespace(preferences=prefs)}))


def _reset_controller():
    snapshot = shared_controller.snapshot()
    if snapshot.active:
        shared_controller.fail("test cleanup")
    if shared_controller.snapshot().state is not BakeState.IDLE:
        shared_controller.reset()


@pytest.fixture(autouse=True)
def clean_controller():
    _reset_controller()
    yield
    _reset_controller()


def test_solver_panel_contains_large_main_bake_action(blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    objects = _objects(env)
    context = _context(env, objects)
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True,
                            "Ready · Protocol 0.11", ("Schema 1",)))
    panel = ui.CLOTHNEXT_PT_solver(); panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert "PPF Contact Solver" in panel.layout.labels
    assert "Ready · Protocol 0.11" not in panel.layout.labels
    assert "Schema 1" not in panel.layout.labels
    env.registration.unregister()


def test_bake_disabled_when_ppf_unavailable(blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    context = _context(env, _objects(env))
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(False, "Not configured"))
    panel = ui.CLOTHNEXT_PT_solver(); panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", False) in panel.layout.operators
    assert "PPF is not configured." in panel.layout.labels
    assert any(item[0] == "clothnext.open_preferences"
               for item in panel.layout.operators)
    env.registration.unregister()


@pytest.mark.parametrize("cloths,colliders,reason", [
    (0, 1, "At least one deformable object is required."),
])
def test_bake_disabled_for_invalid_scene_scope(blender_env, cloths,
                                                colliders, reason):
    env = blender_env; env.registration.register()
    ui = env.physics_ui
    context = _context(env, _objects(env, cloths, colliders))
    model = ui._bake_panel_model(
        context, ui._SolverStatus(True, "Ready · Protocol 0.11"))
    assert not model.enabled and model.reason == reason


def test_bake_allows_scene_without_collider(blender_env):
    env=blender_env; env.registration.register()
    context=_context(env,_objects(env,1,0))
    model=env.physics_ui._bake_panel_model(
        context,env.physics_ui._SolverStatus(True,"Ready · Protocol 0.11"))
    assert model.enabled and model.reason==""
    assert "0 Collider" in model.summary_line
    env.registration.unregister()


def test_large_animated_collider_warns_below_enabled_bake_button(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 150
    collider.cloth_next.collider_motion = "ANIMATED"
    collider.cloth_next.collider_samples_per_frame = 8
    collider.data = SimpleNamespace(vertices=range(214_050))
    context = _context(env, [cloth, collider])
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))

    panel = ui.CLOTHNEXT_PT_solver(); panel.layout = RecordingLayout()
    panel.draw(context)

    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert "Large animated Collider capture: ~2.85 GiB" in panel.layout.labels
    assert "Bake allowed · Low-poly collision proxy recommended." in \
        panel.layout.labels
    env.registration.unregister()


def test_high_collider_gap_and_grip_warn_without_blocking_bake(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    collider.cloth_next.collision.collision_gap = 0.05
    collider.cloth_next.collision.surface_grip = 0.5
    context = _context(env, [cloth, collider])
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))

    panel = ui.CLOTHNEXT_PT_solver(); panel.layout = RecordingLayout()
    panel.draw(context)

    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert "High Collider Gap + Grip can destabilize pinned Cloth." in \
        panel.layout.labels
    assert "Bake allowed - try Gap 0.001 and Grip 0.2-0.3." in \
        panel.layout.labels
    env.registration.unregister()


def test_contact_stability_warning_needs_both_high_values(blender_env):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    context = _context(env, [cloth, collider])
    collider.cloth_next.collision.collision_gap = 0.05
    collider.cloth_next.collision.surface_grip = 0.2
    assert env.physics_ui._contact_stability_warning(context) == ""
    collider.cloth_next.collision.collision_gap = 0.001
    collider.cloth_next.collision.surface_grip = 0.8
    assert env.physics_ui._contact_stability_warning(context) == ""
    env.registration.unregister()


def test_only_extreme_quality_button_uses_red_alert_style(blender_env):
    env = blender_env
    env.registration.register()
    context = _context(env, _objects(env))
    context.scene.cloth_next_quality = SimpleNamespace(
        time_step=0.002, min_newton_steps=1, cg_max_iter=1000,
        cg_tol=0.0001, show_advanced=False)
    drawn = []

    class AlertLayout:
        def __init__(self):
            self.enabled = True
            self.alert = False
            self.use_property_split = False
            self.use_property_decorate = False

        def label(self, **_kw):
            pass

        def row(self, **_kw):
            return AlertLayout()

        def column(self, **_kw):
            return AlertLayout()

        def prop(self, *_args, **_kwargs):
            pass

        def operator(self, _identifier, text="", **_kw):
            drawn.append((text, self.alert))
            return SimpleNamespace()

    env.physics_ui._draw_solver_quality(AlertLayout(), context, False)
    assert drawn == [("Low", False), ("Medium", False), ("High", False),
                     ("Extreme", True)]
    env.registration.unregister()


def test_bake_enabled_for_multiple_deformables(blender_env):
    env = blender_env
    env.registration.register()
    context = _context(env, _objects(env, 2, 1))
    model = env.physics_ui._bake_panel_model(
        context, env.physics_ui._SolverStatus(True, "Ready · Protocol 0.11"))
    assert model.enabled
    assert model.reason == ""
    assert "2 Deformable" in model.summary_line


def test_previous_validation_error_never_locks_out_retry(blender_env,
                                                          monkeypatch):
    env = blender_env
    env.registration.register()
    objects = _objects(env, 1, 0)
    context = _context(env, objects)
    env.physics_ui.validation_state.store_invalid(
        objects[0], "Old Armature/Pinning validation failure")
    monkeypatch.setattr(env.physics_ui, "_cache_state",
                        lambda _context: ("INVALID", "Cache invalid"))

    model = env.physics_ui._bake_panel_model(
        context, env.physics_ui._SolverStatus(True, "Ready"))

    assert model.enabled
    assert model.action == "REBAKE"
    assert model.reason == ""
    env.registration.unregister()


def test_bake_allows_multiple_colliders(blender_env):
    env = blender_env
    env.registration.register()
    context = _context(env, _objects(env, 1, 2))
    model = env.physics_ui._bake_panel_model(
        context, env.physics_ui._SolverStatus(True, "Ready · Protocol 0.11"))
    assert model.enabled
    env.registration.unregister()


def test_new_bake_clears_stale_cancel_before_run_plan(blender_env,
                                                       monkeypatch):
    module = blender_env.solver_test
    context = SimpleNamespace(scene=SimpleNamespace(objects=()))
    module._cancel_event.set()
    monkeypatch.setattr(
        module, "build_run_plan",
        lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(AssertionError("stale cancellation"))
            if module._cancel_event.is_set() else SimpleNamespace()))
    monkeypatch.setattr(module, "_continue_production_bake",
                        lambda _context, job_id, _plan: (job_id, False))

    _job_id, waiting = module.begin_production_bake(context)

    assert waiting is False
    assert not module._cancel_event.is_set()


def test_preparation_window_launches_before_animated_collider_capture(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    blender_env.registration.register()
    cloth, collider = _objects(blender_env, 1, 1)
    collider.cloth_next.collider_motion = "ANIMATED"
    context = _context(blender_env, [cloth, collider])
    snapshot = SimpleNamespace(
        bake_range=module.BakeFrameRange(1, 2), deformables=(),
        collider_objs=(collider,))
    calls = []
    monkeypatch.setattr(module, "validate_scene", lambda _context: snapshot)
    monkeypatch.setattr(module, "build_run_plan",
                        lambda *_args, **_kwargs: calls.append("build") or
                        SimpleNamespace())
    monkeypatch.setattr(module, "_continue_production_bake",
                        lambda _context, job_id, _plan: (job_id, True))
    monkeypatch.setattr(module.companion_manager, "ensure_running",
                        lambda: calls.append("window") or (True, "ready"))

    module.begin_production_bake(context)

    assert calls == ["window", "build"]
    blender_env.registration.unregister()


def test_material_validation_precedes_companion_or_worker(blender_env,
                                                           monkeypatch):
    module = blender_env.solver_test
    context = _context(blender_env, [])
    monkeypatch.setattr(module, "build_run_plan",
                        lambda _c: (_ for _ in ()).throw(
                            module.SceneValidationError("Material settings are invalid.")))
    monkeypatch.setattr(module.companion_manager, "ensure_running",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("companion launched before validation")))
    with pytest.raises(module.SceneValidationError):
        module.start_run(context)
    assert module._worker is None


class _FakeThread:
    def __init__(self, *args, **kwargs):
        self.alive = False
    def start(self): self.alive = True
    def is_alive(self): return self.alive
    def join(self, timeout=None): self.alive = False


def test_production_companion_failure_is_fatal_before_worker(blender_env,
                                                              monkeypatch):
    module = blender_env.solver_test
    plan = SimpleNamespace(frame_start=1, frame_end=8,
                           preset_identifier="COTTON")
    context = _context(blender_env, [], auto_launch=True)
    monkeypatch.setattr(module, "build_run_plan", lambda _c, **_kw: plan)
    monkeypatch.setattr(module.companion_manager, "begin_bake_mode",
                        lambda _request: (False, "Bake executable was not found."))
    with pytest.raises(module.SceneValidationError, match="not found"):
        module.begin_production_bake(context)
    assert module._worker is None
    assert not module.modal_lock.active()


def test_unexpected_bake_preparation_failure_is_visible_and_persisted(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    context = _context(blender_env, [])
    monkeypatch.setattr(module, "build_run_plan",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            RuntimeError("Blender modifier evaluation failed")))
    persisted = []
    monkeypatch.setattr(module.companion_manager, "persist_bake_error",
                        persisted.append)

    with pytest.raises(module.SceneValidationError,
                       match="Preparing the Bake failed"):
        module.begin_production_bake(context)

    snapshot = shared_controller.snapshot()
    assert snapshot.state is BakeState.ERROR
    assert snapshot.error_code
    assert "Blender modifier evaluation failed" in snapshot.error_details
    assert persisted == [snapshot]


def test_bake_validation_failure_is_printed_to_system_console(
        blender_env, monkeypatch, capsys):
    module = blender_env.solver_test
    context = _context(blender_env, [])
    monkeypatch.setattr(module, "begin_production_bake",
                        lambda _context: (_ for _ in ()).throw(
                            module.SceneValidationError(
                                "Animated collider topology changed")))

    operator = module.CLOTHNEXT_OT_bake()
    assert operator.execute(context) == {"CANCELLED"}

    output = capsys.readouterr().out
    assert "[Cloth NeXt] ERROR CNX-" in output
    assert "Animated collider topology changed" in output


def test_pin_capture_uses_wait_cursor_and_modal_input_lock(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    calls = []
    manager = SimpleNamespace(
        event_timer_add=lambda *_a, **_kw: calls.append("timer") or object(),
        event_timer_remove=lambda _timer: calls.append("remove"),
        modal_handler_add=lambda _operator: calls.append("modal"))
    window = SimpleNamespace(
        cursor_modal_set=lambda value: calls.append(("cursor", value)),
        cursor_modal_restore=lambda: calls.append("restore"))
    context = SimpleNamespace(window_manager=manager, window=window,
                              screen=SimpleNamespace(areas=[]))
    monkeypatch.setattr(module, "begin_production_bake",
                        lambda _context: ("job", True))
    module._pin_capture = {"active": True}
    operator = module.CLOTHNEXT_OT_bake()
    assert operator.execute(context) == {"RUNNING_MODAL"}
    assert calls[:3] == ["timer", "modal", ("cursor", "WAIT")]
    module._pin_capture = None
    assert operator.modal(context, SimpleNamespace(type="TIMER")) == {"FINISHED"}
    assert calls[-2:] == ["remove", "restore"]


def test_auto_launch_disabled_starts_without_global_modal_lock(blender_env,
                                                               monkeypatch):
    module = blender_env.solver_test
    plan = SimpleNamespace(frame_start=1, frame_end=8,
                           preset_identifier="COTTON")
    context = _context(blender_env, [], auto_launch=False)
    monkeypatch.setattr(module, "build_run_plan", lambda _c, **_kw: plan)
    calls=[]
    monkeypatch.setattr(module, "prepare_cache_for_new_run",
                        lambda p: calls.append(("cache",p)))
    monkeypatch.setattr(module, "_start_prepared_run",
                        lambda p: calls.append(("run",p)))
    _job, waiting = module.begin_production_bake(context)
    assert not waiting and [x[0] for x in calls] == ["cache","run"]
    assert not module.modal_lock.active()


def test_production_bake_is_responsive_modal_and_cleans_timer_once(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    class Manager:
        def __init__(self):
            self.added = self.removed = self.handlers = 0
        def event_timer_add(self, *_a, **_kw):
            self.added += 1
            return object()
        def event_timer_remove(self, _timer):
            self.removed += 1
        def modal_handler_add(self, _operator):
            self.handlers += 1

    manager = Manager()
    context = SimpleNamespace(window_manager=manager, window=object(),
                              screen=SimpleNamespace(areas=[]))
    plan=SimpleNamespace()
    module._pending_plan=plan; module._pending_job_id="job"
    shared_controller.transition(BakeState.PREPARING,job_id="job")
    shared_controller.transition(BakeState.STARTING_COMPANION)
    shared_controller.transition(BakeState.WAITING_FOR_COMPANION)
    shared_controller.transition(BakeState.COMPANION_READY)
    monkeypatch.setattr(module,"prepare_cache_for_new_run",lambda p:None)
    monkeypatch.setattr(module,"_start_prepared_run",lambda p:
                        shared_controller.transition(BakeState.STARTING_RUN))
    operator = module.CLOTHNEXT_OT_bake_modal(); operator.job_id="job"
    assert operator.invoke(context, None) == {"RUNNING_MODAL"}
    assert (manager.added, manager.handlers) == (1, 1)
    for state in (BakeState.EXPORTING, BakeState.STARTING_SOLVER,
                  BakeState.UPLOADING, BakeState.BUILDING,
                  BakeState.SIMULATING, BakeState.FETCHING,
                  BakeState.IMPORTING, BakeState.FINISHED):
        shared_controller.transition(state)
    event = SimpleNamespace(type="TIMER")
    assert operator.modal(context, event) == {"FINISHED"}
    assert operator.modal(context, event) == {"FINISHED"}
    assert manager.removed == 1


def test_cotton_and_custom_materials_reach_shared_payload(blender_env):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    env.object_properties.apply_preset(cloth.cloth_next, "COTTON")
    cloth.cloth_next.material.preset = "COTTON"
    shell, _static, _contact, preset = env.solver_test._snapshot_materials(
        cloth, collider)
    from cloth_next.ppf.schema.params import shell_wire_params
    assert preset == "COTTON"
    assert shell_wire_params(shell)["young-mod"] == 5500.0
    cloth.cloth_next.material.preset = "CUSTOM"
    cloth.cloth_next.material.stretch_resistance = 4321.0
    shell, _static, _contact, preset = env.solver_test._snapshot_materials(
        cloth, collider)
    assert preset == "CUSTOM"
    assert shell_wire_params(shell)["young-mod"] == 4321.0
    env.registration.unregister()


def test_button_labels_cache_state_progress_cancel_and_reentry(blender_env,
                                                               monkeypatch):
    env = blender_env; env.registration.register(); ui = env.physics_ui
    context = _context(env, _objects(env))
    status = ui._SolverStatus(True, "Ready · Protocol 0.11")
    monkeypatch.setattr(ui, "_cache_state", lambda _c: ("STALE", "Cache stale"))
    assert ui._bake_panel_model(context, status).action == "REBAKE"
    monkeypatch.setattr(ui, "_cache_state", lambda _c: ("MATCHING", "Cache ready"))
    assert ui._bake_panel_model(context, status).action == "BAKE AGAIN"
    shared_controller.transition(BakeState.PREPARING, frame_start=1, frame_end=8)
    shared_controller.transition(BakeState.EXPORTING)
    shared_controller.transition(BakeState.STARTING_SOLVER)
    shared_controller.transition(BakeState.SIMULATING, current_frame=4,
                                 progress_current=4, progress_total=8)
    assert ui._run_state_text(shared_controller.snapshot()) == "Simulating 4 / 8"
    env.solver_test._worker = SimpleNamespace(is_alive=lambda: True)
    env.solver_test.request_cancel()
    assert shared_controller.snapshot().state is BakeState.CANCELLING
    env.solver_test._worker = None
    shared_controller.transition(BakeState.CANCELLED)
    assert env.solver_test.CLOTHNEXT_OT_bake.poll(context)
    env.registration.unregister()


def test_no_native_cloth_modifier_added_by_production_bake():
    source = Path("cloth_next/blender/solver_test.py").read_text(encoding="utf-8")
    assert 'type="CLOTH"' not in source
    assert 'type="MESH_CACHE"' in source
