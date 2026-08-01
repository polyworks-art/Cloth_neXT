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
            self.containers = []
            self.properties = []
        self.enabled = True
        self.alert = False
        self.scale_y = 1.0

    def label(self, text="", **_kw):
        self.sink.labels.append(text)

    def operator(self, identifier, text="", **_kw):
        self.sink.operators.append((identifier, text, self.enabled))
        return SimpleNamespace()

    def prop(self, data, property_name, **_kw):
        self.sink.properties.append((data, property_name, self.enabled))

    def row(self, **_kw):
        self.sink.containers.append("row")
        return RecordingLayout(self.sink)

    def split(self, **_kw):
        self.sink.containers.append("split")
        return RecordingLayout(self.sink)

    def column(self, **_kw):
        self.sink.containers.append("column")
        return RecordingLayout(self.sink)

    def box(self):
        self.sink.containers.append("box")
        return RecordingLayout(self.sink)


def _objects(env, cloth_count=1, collider_count=1):
    result = []
    for number in range(cloth_count):
        obj = env.bpy.types.Object(name=f"Cloth{number}", type="MESH")
        obj.cloth_next.enabled = True
        obj.cloth_next.role = "CLOTH"
        # A production-ready scene has a chosen cache folder; without one the
        # Bake button is deliberately disabled (results would be lost on a
        # Blender restart). Tests that exercise that gate clear this.
        obj.cloth_next.cache_directory = "//cn_cache/"
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


def test_mismatched_bake_ranges_name_every_object(blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    objects = _objects(env, cloth_count=2, collider_count=0)
    objects[0].cloth_next.bake_start = 1
    objects[0].cloth_next.bake_end = 100
    objects[1].cloth_next.bake_start = 2
    objects[1].cloth_next.bake_end = 90
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    model = ui._bake_panel_model(_context(env, objects))
    assert not model.enabled
    assert "Cloth0: 1–100" in model.reason
    assert "Cloth1: 2–90" in model.reason
    env.registration.unregister()


def test_use_scene_range_updates_every_enabled_deformable(blender_env):
    env = blender_env; env.registration.register()
    objects = _objects(env, cloth_count=2, collider_count=1)
    objects[1].cloth_next.role = "RIGID_BODY"
    objects[0].cloth_next.bake_end = 20
    objects[1].cloth_next.bake_end = 80
    context = _context(env, objects)
    context.active_object = objects[0]
    context.scene.frame_start = 5
    context.scene.frame_end = 42
    operator = env.physics_operators.CLOTHNEXT_OT_use_scene_range()
    assert operator.execute(context) == {"FINISHED"}
    assert [(obj.cloth_next.bake_start, obj.cloth_next.bake_end)
            for obj in objects[:2]] == [(5, 42), (5, 42)]
    env.registration.unregister()


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
    split_index = panel.layout.containers.index("split")
    assert panel.layout.containers[split_index:split_index + 3] == [
        "split", "column", "column"]
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


def test_new_bake_attempt_clears_previous_intersection_overlay(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    module = env.solver_test
    from cloth_next import intersection_diagnostics
    from cloth_next.blender import intersection_overlay

    violation = intersection_diagnostics.IntersectionViolation(
        classification="SELF_INTERSECTION",
        detection_method="SOLVER_REPORTED",
        elements=(),
        combined_pair=(1, 2),
        total_count=1)
    module._intersection_violations = (violation,)
    intersection_overlay.set_violations((violation,), None)
    monkeypatch.setattr(
        module, "validate_scene",
        lambda _context: (_ for _ in ()).throw(
            module.SceneValidationError("new attempt validation stopped")))
    context = _context(env, _objects(env))

    with pytest.raises(module.SceneValidationError):
        module.begin_production_bake(context)

    assert module.intersection_violations() == ()
    assert intersection_overlay.current() is None
    env.registration.unregister()


def test_selected_registry_solver_enables_bake(blender_env, monkeypatch,
                                                tmp_path):
    env = blender_env; env.registration.register()
    from cloth_next.updater.install_paths import ManagedSolverPaths
    from cloth_next.updater.solver_registry import (
        SolverInstallation, SolverRegistry, write_registry)

    root = tmp_path / "official-013"
    executable = root / "bin" / "ppf-contact-solver.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"solver")
    installation = SolverInstallation(
        installation_id="official-013",
        display_name="PPF 0.13",
        source="official",
        root_path=str(root),
        executable_path=str(executable),
        frontend_path=None,
        package_version="0.2.0",
        protocol_version="0.13",
        schema_version="2",
        official_release_tag="test-013",
        managed=True,
        verified=True,
        healthy=True,
        channel="current")
    paths = ManagedSolverPaths(tmp_path / "managed")
    write_registry(
        paths.registry_json,
        SolverRegistry((installation,), installation.installation_id))
    monkeypatch.setattr(ManagedSolverPaths, "default",
                        classmethod(lambda cls: paths))

    context = _context(env, _objects(env))
    context.preferences.addons[
        "cloth_next"].preferences.selected_solver_installation_id = (
            installation.installation_id)
    status = env.physics_ui._solver_status(context)
    model = env.physics_ui._bake_panel_model(context, status)

    assert status.ready
    assert status.title == "Ready Â· Protocol 0.13"
    assert "Schema 2" in status.details
    assert model.enabled
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


def test_bake_disabled_without_cache_directory(blender_env):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    cloth.cloth_next.cache_directory = ""
    context = _context(env, [cloth, collider])
    ui = env.physics_ui
    model = ui._bake_panel_model(
        context, ui._SolverStatus(True, "Ready · Protocol 0.11"))
    assert not model.enabled
    assert "Cache Directory" in model.reason
    env.registration.unregister()


def test_solver_panel_offers_cache_directory_button(blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    cloth.cloth_next.cache_directory = ""  # requirement unmet
    context = _context(env, [cloth, collider])
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_solver(); panel.layout = RecordingLayout()
    panel.draw(context)
    # The set-directory button is present and stays enabled even though the
    # Bake button itself is disabled, so the artist can satisfy the gate.
    assert ("clothnext.set_cache_directory", "", True) in panel.layout.operators
    assert ("clothnext.bake", "BAKE", False) in panel.layout.operators
    env.registration.unregister()


def test_cloth_simulation_places_bake_directory_and_statistics_together(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    cloth.cloth_next.bake_end = 180
    force = env.bpy.types.Object(name="Wind", type="EMPTY")
    force.cloth_next.enabled = True
    force.cloth_next.role = "FORCE"
    context = _context(env, [cloth, collider, force])
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert ("clothnext.set_cache_directory", "", True) in panel.layout.operators
    assert not any(identifier == "clothnext.validate"
                   for identifier, _text, _enabled in panel.layout.operators)
    assert "1 Deformables · 1 Collider · 1 Forces" in panel.layout.labels
    assert "Frames 1–180" in panel.layout.labels
    env.registration.unregister()


def test_primary_simulation_panel_exposes_provisional_recovery_banner(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    context = _context(env, _objects(env))
    context.scene.cloth_next_recovery = SimpleNamespace(
        enabled=True, status="Checkpoint Found", status_detail="Verified",
        compatible=False, resumable=True, latest_checkpoint_frame=12)
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()

    panel.draw(context)

    assert "Recovery checkpoint found \u00b7 Frame 12" in panel.layout.labels
    assert "Compatibility will be verified before Resume" in panel.layout.labels
    assert ("clothnext.recovery_resume_latest", "Resume Bake", True) \
        in panel.layout.operators
    assert ("clothnext.recovery_start_fresh", "Start Fresh", True) \
        in panel.layout.operators
    env.registration.unregister()


def test_live_preview_checkbox_is_in_primary_simulation_panel_below_bake(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    context = _context(env, _objects(env))
    preview = SimpleNamespace(enabled=False, status="Newton unavailable",
                              status_detail="", bake_backend="NEWTON")
    context.scene.cloth_next_newton_preview = preview
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation(); panel.layout = RecordingLayout()

    panel.draw(context)

    assert (preview, "enabled", True) in panel.layout.properties
    # Bake is emitted first, then the Live Preview property, before any
    # Recovery banner actions in the production panel draw.
    assert panel.layout.operators[0][0] == "clothnext.bake"
    env.registration.unregister()


def test_live_preview_is_hidden_for_production_solver(blender_env,
                                                       monkeypatch):
    env = blender_env; env.registration.register()
    context = _context(env, _objects(env))
    preview = SimpleNamespace(
        enabled=False, status="Newton unavailable", status_detail="",
        bake_backend="PPF")
    context.scene.cloth_next_newton_preview = preview
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation(); panel.layout = RecordingLayout()

    panel.draw(context)

    assert not any(obj is preview and name == "enabled"
                   for obj, name, _enabled in panel.layout.properties)
    env.registration.unregister()


@pytest.mark.parametrize("enabled", (False, True))
def test_live_preview_toggle_uses_only_the_current_newton_action_icon(
        blender_env, monkeypatch, enabled):
    env = blender_env; env.registration.register()
    context = _context(env, _objects(env))
    context.scene.cloth_next_newton_preview = SimpleNamespace(
        enabled=enabled, status="Live", status_detail="", quality="BALANCED",
        enable_self_contact=True, time_scale=1.0, bake_backend="NEWTON")
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation(); panel.layout = RecordingLayout()

    panel.draw(context)

    preview_properties = [item for item in panel.layout.properties
                          if item[0] is context.scene.cloth_next_newton_preview
                          and item[1] == "enabled"]
    assert len(preview_properties) == 1
    # RecordingLayout stores property identity only; verify the draw source
    # uses an icon-only toggle with the two unambiguous states.
    source = Path(ui.__file__).read_text(encoding="utf-8")
    assert 'row.prop(settings, "enabled", text="",' in source
    assert 'icon="PAUSE" if settings.enabled else "PLAY"' in source
    assert not any(identifier == "screen.animation_play"
                   for identifier, _text, _enabled in panel.layout.operators)
    env.registration.unregister()


def test_cloth_simulation_active_bake_has_no_blender_progress_details(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    context = _context(env, _objects(env))
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    shared_controller.transition(BakeState.PREPARING, job_id="ui-test")
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert "Bake running in Cloth NeXt Bake Window" in panel.layout.labels
    assert any(identifier == "clothnext.bake_cancel"
               for identifier, _text, _enabled in panel.layout.operators)
    assert not any("Frame " in label or "ETA" in label
                   for label in panel.layout.labels)
    assert "progress" not in panel.layout.containers
    env.registration.unregister()


def test_soft_body_simulation_reuses_bake_action_and_scene_statistics(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    deformables = _objects(env, cloth_count=2, collider_count=2)
    soft, other, collider_a, collider_b = deformables
    soft.cloth_next.role = "SOFT_BODY"
    other.cloth_next.role = "RIGID_BODY"
    soft.cloth_next.bake_end = 180
    other.cloth_next.bake_end = 180
    force = env.bpy.types.Object(name="Wind", type="EMPTY")
    force.cloth_next.enabled = True
    force.cloth_next.role = "FORCE"
    context = _context(
        env, [soft, other, collider_a, collider_b, force])
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert ("clothnext.set_cache_directory", "", True) in \
        panel.layout.operators
    assert "2 Deformables · 2 Colliders · 1 Force" in panel.layout.labels
    assert "Frames 1–180" in panel.layout.labels
    assert not any(identifier == "clothnext.validate"
                   for identifier, _text, _enabled in panel.layout.operators)
    assert "progress" not in panel.layout.containers
    env.registration.unregister()


def test_rigid_body_simulation_uses_role_specific_scene_statistics(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    objects = _objects(env, cloth_count=3, collider_count=2)
    rigid, cloth, soft, collider_a, collider_b = objects
    rigid.cloth_next.role = "RIGID_BODY"
    soft.cloth_next.role = "SOFT_BODY"
    for deformable in (rigid, cloth, soft):
        deformable.cloth_next.bake_end = 180
    context = _context(
        env, [rigid, cloth, soft, collider_a, collider_b])
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert ("clothnext.set_cache_directory", "", True) in \
        panel.layout.operators
    assert "1 Rigid Body · 2 Deformables · 2 Colliders" in \
        panel.layout.labels
    assert "Frames 1–180" in panel.layout.labels
    assert not any(identifier == "clothnext.validate"
                   for identifier, _text, _enabled in panel.layout.operators)
    assert "progress" not in panel.layout.containers
    env.registration.unregister()


def test_cable_rope_simulation_reuses_bake_and_compact_statistics(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    cable, cloth, collider = _objects(
        env, cloth_count=2, collider_count=1)
    cable.type = "CURVE"
    cable.data = SimpleNamespace(get=lambda _key, default="": default)
    cable.cloth_next.role = "ROD"
    cable.cloth_next.bake_end = 180
    cloth.cloth_next.bake_end = 180
    force = env.bpy.types.Object(name="Wind", type="EMPTY")
    force.cloth_next.enabled = True
    force.cloth_next.role = "FORCE"
    context = _context(env, [cable, cloth, collider, force])
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert ("clothnext.set_cache_directory", "", True) in \
        panel.layout.operators
    assert "2 Deformables · 1 Collider · 1 Force" in panel.layout.labels
    assert "Frames 1–180" in panel.layout.labels
    assert not any(identifier == "clothnext.validate"
                   for identifier, _text, _enabled in panel.layout.operators)
    assert "progress" not in panel.layout.containers
    env.registration.unregister()


def test_collider_simulation_starts_global_bake_without_quality_row(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    objects = _objects(env, cloth_count=3, collider_count=2)
    cloth_a, cloth_b, cloth_c, active_collider, other_collider = objects
    for deformable in (cloth_a, cloth_b, cloth_c):
        deformable.cloth_next.bake_end = 180
    force_a = env.bpy.types.Object(name="Wind", type="EMPTY")
    force_a.cloth_next.enabled = True
    force_a.cloth_next.role = "FORCE"
    force_b = env.bpy.types.Object(name="Gravity", type="EMPTY")
    force_b.cloth_next.enabled = True
    force_b.cloth_next.role = "FORCE"
    ordered = [
        active_collider, cloth_a, cloth_b, cloth_c, other_collider,
        force_a, force_b]
    context = _context(env, ordered)
    context.scene.cloth_next_quality = SimpleNamespace(
        time_step=0.0025, min_newton_steps=8, cg_max_iter=10_000,
        cg_tol=0.001, show_advanced=False)
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert ("clothnext.set_cache_directory", "", True) in \
        panel.layout.operators
    assert "Quality" not in panel.layout.labels
    assert "3 Deformables · 2 Colliders · 2 Forces" in panel.layout.labels
    assert "Frames 1–180" in panel.layout.labels
    assert not any(identifier == "clothnext.validate"
                   for identifier, _text, _enabled in panel.layout.operators)
    assert "progress" not in panel.layout.containers
    env.registration.unregister()


def test_force_simulation_starts_global_bake_without_quality_row(
        blender_env, monkeypatch):
    env = blender_env; env.registration.register()
    objects = _objects(env, cloth_count=3, collider_count=1)
    cloth_a, cloth_b, cloth_c, collider = objects
    for deformable in (cloth_a, cloth_b, cloth_c):
        deformable.cloth_next.bake_end = 180
    active_force = env.bpy.types.Object(name="Wind", type="EMPTY")
    active_force.cloth_next.enabled = True
    active_force.cloth_next.role = "FORCE"
    other_force = env.bpy.types.Object(name="Gravity", type="EMPTY")
    other_force.cloth_next.enabled = True
    other_force.cloth_next.role = "FORCE"
    context = _context(
        env, [active_force, cloth_a, cloth_b, cloth_c, collider, other_force])
    context.scene.cloth_next_quality = SimpleNamespace(
        time_step=0.0025, min_newton_steps=8, cg_max_iter=10_000,
        cg_tol=0.001, show_advanced=False)
    ui = env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready"))
    panel = ui.CLOTHNEXT_PT_simulation()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert ("clothnext.bake", "BAKE", True) in panel.layout.operators
    assert ("clothnext.set_cache_directory", "", True) in \
        panel.layout.operators
    assert "Quality" not in panel.layout.labels
    statistics = panel.layout.labels[-2]
    assert all(text in statistics for text in (
        "3 Deformables", "1 Collider", "2 Forces"))
    assert panel.layout.labels[-1].startswith("Frames 1")
    assert panel.layout.labels[-1].endswith("180")
    assert not any(identifier == "clothnext.validate"
                   for identifier, _text, _enabled in panel.layout.operators)
    assert "progress" not in panel.layout.containers
    env.registration.unregister()


def test_require_cache_directories_names_missing_objects(blender_env):
    module = blender_env.solver_test
    with_dir = SimpleNamespace(
        name="A", cloth_next=SimpleNamespace(cache_directory="//c/"))
    without_dir = SimpleNamespace(
        name="B", cloth_next=SimpleNamespace(cache_directory=""))
    module._require_cache_directories((with_dir,))  # all set: no raise
    with pytest.raises(module.SceneValidationError, match="B"):
        module._require_cache_directories((with_dir, without_dir))


def test_set_cache_directory_operator_applies_to_all_deformables(blender_env):
    env = blender_env; env.registration.register()
    cloth, collider = _objects(env)
    cloth.cloth_next.cache_directory = ""
    context = _context(env, [cloth, collider])
    operator = env.solver_test.CLOTHNEXT_OT_set_cache_directory()
    operator.directory = "//chosen_cache/"
    assert operator.execute(context) == {"FINISHED"}
    assert cloth.cloth_next.cache_directory == "//chosen_cache/"
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
    assert "High Collision Gap and Friction can destabilize pinned Cloth." in \
        panel.layout.labels
    assert "Bake allowed · Try Gap 0.001 and Friction 0.2–0.3" in \
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


def test_mixed_scene_locks_low_and_uses_x_quality_buttons(blender_env):
    env = blender_env
    env.registration.register()
    objects = _objects(env)
    rigid = env.bpy.types.Object(name="Rigid", type="MESH")
    rigid.cloth_next.enabled = True
    rigid.cloth_next.role = "RIGID_BODY"
    objects.append(rigid)
    context = _context(env, objects)
    context.scene.cloth_next_quality = SimpleNamespace(
        time_step=0.005, min_newton_steps=6, cg_max_iter=10000,
        cg_tol=0.001, show_advanced=False)
    drawn = []

    class QualityLayout:
        def __init__(self):
            self.enabled = True
            self.alert = False

        def label(self, **_kw):
            pass

        def row(self, **_kw):
            return QualityLayout()

        def operator(self, identifier, text="", **kw):
            drawn.append((
                identifier, text, self.enabled, kw.get("depress", False)))
            return SimpleNamespace()

    env.physics_ui._draw_quality_selector(QualityLayout(), context, False)
    assert [(text, enabled, depressed)
            for _identifier, text, enabled, depressed in drawn] == [
        ("Low", False, False),
        ("XMedium", True, True),
        ("XHigh", True, False),
        ("XExtreme", True, False),
    ]
    assert [identifier for identifier, *_rest in drawn[1:]] == [
        env.physics_operators.PDRD_QUALITY_PRESET_OPERATOR_IDS[preset]
        for preset in ("MEDIUM", "HIGH", "EXTREME")]
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
    context.scene.frame_current = 1
    context.scene.frame_set = lambda frame: setattr(
        context.scene, "frame_current", frame)
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

    _job_id, waiting = module.begin_production_bake(context)

    assert waiting is True
    assert calls == ["window"]
    assert module._pin_capture is not None
    assert module._pin_capture["wait_for_companion"] is True
    module.request_cancel()
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
