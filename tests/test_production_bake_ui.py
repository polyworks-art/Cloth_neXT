# SPDX-License-Identifier: GPL-3.0-or-later
"""Production Solver-panel Bake entry point and shared run-service contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.bake.controller import shared_controller
from cloth_next.bake.status import BakeJobKind, BakeState


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
    assert "Ready · Protocol 0.11" in panel.layout.labels
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
    (0, 1, "Exactly one Cloth object is currently supported."),
    (2, 1, "Exactly one Cloth object is currently supported."),
    (1, 0, "Exactly one static Collider is currently supported."),
    (1, 2, "Exactly one static Collider is currently supported."),
])
def test_bake_disabled_for_invalid_scene_scope(blender_env, cloths,
                                                colliders, reason):
    env = blender_env; env.registration.register()
    ui = env.physics_ui
    context = _context(env, _objects(env, cloths, colliders))
    model = ui._bake_panel_model(
        context, ui._SolverStatus(True, "Ready · Protocol 0.11"))
    assert not model.enabled and model.reason == reason
    env.registration.unregister()


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


def _start_with_stubs(env, monkeypatch, *, auto_launch, companion_result):
    module = env.solver_test
    plan = SimpleNamespace(work_directory=Path("/tmp/run"),
                           cloth_object_name="Cloth")
    context = _context(env, [], auto_launch=auto_launch)
    calls = []
    monkeypatch.setattr(module, "build_run_plan", lambda _c: plan)
    monkeypatch.setattr(module.companion_manager, "ensure_running",
                        lambda: calls.append("ensure") or companion_result)
    monkeypatch.setattr(module.threading, "Thread", _FakeThread)
    warning = module.start_run(context, job_kind=BakeJobKind.BAKE)
    assert module._active_plan is plan
    module.shutdown(join_timeout=0)
    return calls, warning


def test_bake_auto_launch_preference_and_optional_failure(blender_env,
                                                           monkeypatch):
    calls, warning = _start_with_stubs(
        blender_env, monkeypatch, auto_launch=True,
        companion_result=(False, "missing"))
    assert calls == ["ensure"]
    assert warning == "Bake window could not be opened; simulation continues in Blender."
    assert shared_controller.snapshot().state is BakeState.EXPORTING


def test_bake_skips_companion_when_auto_launch_disabled(blender_env,
                                                        monkeypatch):
    calls, warning = _start_with_stubs(
        blender_env, monkeypatch, auto_launch=False,
        companion_result=(True, "unexpected"))
    assert calls == [] and warning == ""


def test_unexpected_companion_error_is_warning_and_bake_continues(
        blender_env, monkeypatch):
    module = blender_env.solver_test
    plan = SimpleNamespace(work_directory=Path("/tmp/run"),
                           cloth_object_name="Cloth")
    context = _context(blender_env, [], auto_launch=True)
    monkeypatch.setattr(module, "build_run_plan", lambda _c: plan)
    monkeypatch.setattr(module.companion_manager, "ensure_running",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(module.threading, "Thread", _FakeThread)
    warning = module.start_run(context)
    assert warning == "Bake window could not be opened; simulation continues in Blender."
    assert module.run_active()
    module.shutdown(join_timeout=0)


def test_production_and_developer_operators_share_run_service(blender_env,
                                                              monkeypatch):
    module = blender_env.solver_test
    kinds = []
    monkeypatch.setattr(module, "start_run",
                        lambda _c, *, job_kind: kinds.append(job_kind) or "")
    assert module.CLOTHNEXT_OT_bake().execute(blender_env.bpy.context) == {"FINISHED"}
    assert module.CLOTHNEXT_OT_solver_test_run().execute(
        blender_env.bpy.context) == {"FINISHED"}
    assert kinds == [BakeJobKind.BAKE, BakeJobKind.SOLVER_TEST]


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
