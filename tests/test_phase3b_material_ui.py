# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Phase-3B Blender material UI and bridge contracts (fake ``bpy``).

Real-Blender behavior (live update callbacks, undo, RNA registration) is
covered by ``tools/blender_smoke_test.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.materials import presets as material_presets
from tests import fake_bpy

REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_PACKAGE = REPO_ROOT / "cloth_next" / "blender"


class RecordingLayout:
    """Just enough UILayout for panel draw calls."""

    def __init__(self, sink=None):
        self.sink = sink if sink is not None else self
        if self.sink is self:
            self.props: list[str] = []
            self.labels: list[str] = []
        self.use_property_split = False
        self.use_property_decorate = False
        self.enabled = True

    def prop(self, _data, name, **_kw):
        self.sink.props.append(name)

    def label(self, text="", **_kw):
        self.sink.labels.append(text)

    def column(self, align=False):
        return RecordingLayout(self.sink)

    def row(self, align=False):
        return RecordingLayout(self.sink)

    def box(self):
        return RecordingLayout(self.sink)

    def operator(self, *_a, **_kw):
        return SimpleNamespace()


def _settings(env):
    obj = env.bpy.types.Object(name="Cloth", type="MESH")
    settings = obj.cloth_next  # materializes the PropertyGroup defaults
    return obj, settings


def _context(obj):
    return SimpleNamespace(object=obj, active_object=obj)


# --- registration and property model -----------------------------------------

def test_new_material_property_groups_register(blender_env):
    env = blender_env
    env.registration.register()
    names = [cls.__name__ for cls in env.bpy.registry]
    for expected in ("CLOTHNEXT_PG_material_settings",
                     "CLOTHNEXT_PG_damping_settings",
                     "CLOTHNEXT_PG_collision_settings",
                     "CLOTHNEXT_PG_object_settings"):
        assert expected in names
    env.registration.unregister()


def test_old_placeholder_properties_are_gone():
    source = (BLENDER_PACKAGE / "object_properties.py").read_text(
        encoding="utf-8")
    for forbidden in ("stretch_stiffness", "shear_stiffness",
                      "bend_stiffness", "thickness:", "mass_mode",
                      "self_collision", "self_distance",
                      "volume_conservation", "pin_group", "rest_shape",
                      "CLOTHNEXT_PG_quality_settings",
                      "CLOTHNEXT_PG_physical_settings",
                      "CLOTHNEXT_PG_pressure_settings",
                      "CLOTHNEXT_PG_shape_settings",
                      "CLOTHNEXT_PG_cache_settings"):
        assert forbidden not in source, forbidden


def test_defaults_are_default_cloth(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    material = settings.material
    assert material.preset == "DEFAULT_CLOTH"
    assert material.model == "FABRIC"
    assert material.surface_density == 1.0
    assert material.stretch_resistance == 1000.0
    assert material.sideways_response == 0.35
    assert material.bend_resistance == 10.0
    assert material.stretch_limit_enabled is False
    assert settings.damping.deformation_damping == 0.0
    assert settings.collision.enabled is True
    assert settings.collision.surface_grip == 0.5
    assert settings.collision.contact_gap == 0.001
    assert settings.collision.contact_offset == 0.0
    env.registration.unregister()


def test_tooltips_disclose_effect_unit_and_ppf_parameter(blender_env):
    env = blender_env
    props = fake_bpy._resolved_props(
        env.object_properties.CLOTHNEXT_PG_material_settings)
    dampings = fake_bpy._resolved_props(
        env.object_properties.CLOTHNEXT_PG_damping_settings)
    collisions = fake_bpy._resolved_props(
        env.object_properties.CLOTHNEXT_PG_collision_settings)
    expectations = {
        props["surface_density"]: ("kg/m²", "shell density"),
        props["stretch_resistance"]: ("density-normalized", "young-mod"),
        props["sideways_response"]: ("poiss-rat",),
        props["bend_resistance"]: ("bend",),
        props["stretch_limit_enabled"]: ("strain limit",),
        props["maximum_stretch_percent"]: ("strain-limit",),
        props["model"]: ("Baraff-Witkin", "ARAP", "model"),
        dampings["deformation_damping"]: ("seconds", "deformation-damping"),
        dampings["bending_damping"]: ("seconds", "bending-damping"),
        collisions["surface_grip"]: ("friction", "Minimum"),
        collisions["contact_gap"]: ("world units", "contact-gap"),
        collisions["contact_offset"]: ("world units", "contact-offset"),
    }
    for prop, needles in expectations.items():
        description = prop.keywords["description"]
        for needle in needles:
            assert needle in description, (prop.keywords["name"], needle)


def test_property_ranges_match_the_pinned_upstream_ui(blender_env):
    props = fake_bpy._resolved_props(
        blender_env.object_properties.CLOTHNEXT_PG_material_settings)
    stretch = props["stretch_resistance"].keywords
    assert stretch["min"] == 0.0 and stretch["soft_max"] == 100000.0
    assert stretch["max"] == 1e9
    assert props["sideways_response"].keywords["max"] == 0.4999
    assert props["surface_density"].keywords["min"] > 0.0
    assert props["surface_density"].keywords["max"] == 10000.0
    assert props["maximum_stretch_percent"].keywords["max"] == 100.0
    assert props["maximum_stretch_percent"].keywords["min"] > 0.0


# --- preset behavior ----------------------------------------------------------

def test_preset_items_are_builtin_order_plus_custom(blender_env):
    items = blender_env.object_properties.PRESET_ITEMS
    identifiers = [item[0] for item in items]
    assert identifiers == ["DEFAULT_CLOTH", "SILK", "FLAG", "COTTON",
                           "WOOL", "DENIM", "LEATHER", "CUSTOM"]
    assert items[-1][1] == "Custom"


def test_apply_preset_is_deterministic_and_exact(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    assert env.object_properties.apply_preset(settings, "SILK") is True
    material = settings.material
    assert material.model == "FABRIC"
    assert material.surface_density == 1.0
    assert material.stretch_resistance == 500.0
    assert material.sideways_response == 0.4
    assert material.bend_resistance == 1.42
    assert material.stretch_limit_enabled is True
    assert material.maximum_stretch_percent == 6.0
    assert settings.collision.surface_grip == 0.25
    assert settings.damping.deformation_damping == 0.0
    # deterministic: applying twice yields the identical state
    snapshot = env.object_properties.shell_settings_from(settings)
    env.object_properties.apply_preset(settings, "SILK")
    assert env.object_properties.shell_settings_from(settings) == snapshot
    env.registration.unregister()


def test_apply_unknown_preset_is_atomic_noop(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    before = env.object_properties.shell_settings_from(settings)
    assert env.object_properties.apply_preset(settings, "NO_SUCH") is False
    assert env.object_properties.shell_settings_from(settings) == before
    env.registration.unregister()


def test_manual_edit_marks_preset_custom_without_reset(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    env.object_properties.apply_preset(settings, "COTTON")
    settings.material.preset = "COTTON"
    settings.material.bend_resistance = 99.0  # manual edit (fake bpy: no
    # update callbacks fire, so invoke the shared handler directly)
    env.object_properties.mark_custom(settings)
    assert settings.material.preset == "CUSTOM"
    # nothing was reset by switching to Custom
    assert settings.material.bend_resistance == 99.0
    assert settings.material.stretch_resistance == 5500.0
    env.registration.unregister()


def test_selecting_custom_never_alters_values(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    env.object_properties.apply_preset(settings, "DENIM")
    material = settings.material
    material.preset = "CUSTOM"
    fake_self = SimpleNamespace(
        preset="CUSTOM", id_data=SimpleNamespace(cloth_next=settings))
    env.object_properties._on_preset_update(fake_self, None)
    assert material.stretch_resistance == 10000.0
    assert material.bend_resistance == 10.0
    env.registration.unregister()


def test_preset_update_callback_applies_via_id_data(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.material.preset = "WOOL"
    fake_self = SimpleNamespace(preset="WOOL",
                                id_data=SimpleNamespace(cloth_next=settings))
    env.object_properties._on_preset_update(fake_self, None)
    assert settings.material.stretch_resistance == 2000.0
    assert settings.material.maximum_stretch_percent == 8.0
    env.registration.unregister()


def test_mark_custom_suppressed_while_preset_applies(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    settings.material.preset = "COTTON"
    env.object_properties._applying_preset = True
    try:
        env.object_properties.mark_custom(settings)
        assert settings.material.preset == "COTTON"
    finally:
        env.object_properties._applying_preset = False
    env.registration.unregister()


def test_panel_draw_never_reads_the_preset_file(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    material_presets.builtin_presets()  # ensure the cache is warm
    monkeypatch.setattr(material_presets, "_PRESET_FILE",
                        Path("Z:/nonexistent/presets.toml"))
    monkeypatch.setattr(material_presets, "parse_presets",
                        lambda text: (_ for _ in ()).throw(
                            AssertionError("draw parsed the preset file")))
    obj, settings = _settings(env)
    panel = env.physics_ui.CLOTHNEXT_PT_material()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert "preset" in panel.layout.props
    env.registration.unregister()


# --- honest UI ----------------------------------------------------------------

def test_unsupported_panels_are_gone(blender_env):
    ui = blender_env.physics_ui
    names = [cls.__name__ for cls in ui.CLASSES]
    for forbidden in ("CLOTHNEXT_PT_quality", "CLOTHNEXT_PT_pressure",
                      "CLOTHNEXT_PT_shape", "CLOTHNEXT_PT_physical"):
        assert forbidden not in names
    assert "CLOTHNEXT_PT_material" in names


def test_material_panel_displays_artist_facing_names(blender_env):
    env = blender_env
    env.registration.register()
    obj, _unused = _settings(env)
    panel = env.physics_ui.CLOTHNEXT_PT_material()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.props == ["preset", "surface_density",
                                  "stretch_resistance", "sideways_response",
                                  "bend_resistance",
                                  "stretch_limit_enabled",
                                  "maximum_stretch_percent"]
    assert "Fabric Behavior" in panel.layout.labels
    assert "Stretch Protection" in panel.layout.labels
    env.registration.unregister()


def test_cache_panel_shows_readonly_development_slice(blender_env):
    env = blender_env
    env.registration.register()
    obj, _unused = _settings(env)
    panel = env.physics_ui.CLOTHNEXT_PT_cache()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert any("frames 1–8" in label for label in panel.layout.labels)
    assert "frame_start" not in panel.layout.props
    assert "frame_end" not in panel.layout.props
    env.registration.unregister()


def test_collider_collisions_show_only_contact_values(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.role = "COLLIDER"
    panel = env.physics_ui.CLOTHNEXT_PT_collisions()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.props == ["surface_grip", "contact_gap",
                                  "contact_offset"]
    settings.role = "CLOTH"
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.props == ["enabled", "surface_grip", "contact_gap",
                                  "contact_offset"]
    env.registration.unregister()


def test_advanced_panel_shows_exact_wire_names_and_friction_mode(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    panel = env.physics_ui.CLOTHNEXT_PT_advanced()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    text = "\n".join(panel.layout.labels)
    for needle in ("young-mod", "poiss-rat", "bend", "friction",
                   "contact-gap", "contact-offset", "strain-limit",
                   "Minimum", "density-normalized"):
        assert needle in text, needle
    assert "model" in panel.layout.props
    settings.role = "COLLIDER"
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert "model" not in panel.layout.props
    assert "young-mod" not in "\n".join(panel.layout.labels)
    env.registration.unregister()


def test_no_fake_editable_controls_remain_in_ui_source():
    source = (BLENDER_PACKAGE / "physics_ui.py").read_text(encoding="utf-8")
    for forbidden in ("substeps", "solver_iterations", "contact_iterations",
                      "thickness", "stretch_stiffness", "shear",
                      "self_collision", "pin_group", "rest_scale",
                      "volume_conservation", '"frame_start"',
                      '"frame_end"'):
        assert forbidden not in source, forbidden


# --- bridge: snapshot, validation before worker --------------------------------

def test_snapshot_materials_returns_pure_immutable_settings(blender_env):
    env = blender_env
    env.registration.register()
    cloth_obj, cloth_settings = _settings(env)
    collider_obj, collider_settings = _settings(env)
    env.object_properties.apply_preset(cloth_settings, "COTTON")
    collider_settings.collision.surface_grip = 0.9
    shell, static, contact_enabled, preset = \
        env.solver_test._snapshot_materials(cloth_obj, collider_obj)
    from cloth_next.materials import (ShellMaterialSettings,
                                      StaticMaterialSettings)
    assert isinstance(shell, ShellMaterialSettings)
    assert isinstance(static, StaticMaterialSettings)
    assert shell.stretch_resistance == 5500.0
    assert static.surface_grip == 0.9
    assert contact_enabled is True
    with pytest.raises(Exception):
        shell.stretch_resistance = 1.0  # frozen dataclass
    env.registration.unregister()


def test_invalid_material_fails_validation_before_any_worker(blender_env):
    env = blender_env
    env.registration.register()
    cloth_obj, cloth_settings = _settings(env)
    collider_obj, _collider_settings = _settings(env)
    cloth_settings.material.sideways_response = 0.75  # out of range
    with pytest.raises(env.solver_test.SceneValidationError) as excinfo:
        env.solver_test._snapshot_materials(cloth_obj, collider_obj)
    message = str(excinfo.value)
    assert "sideways_response" in message and "0.4999" in message
    env.registration.unregister()


def test_validation_failure_starts_no_solver_worker(blender_env, monkeypatch):
    env = blender_env
    module = env.solver_test
    env.registration.register()
    monkeypatch.setattr(module, "build_run_plan",
                        lambda _context: (_ for _ in ()).throw(
                            module.SceneValidationError("bad material")))
    monkeypatch.setattr(module.companion_manager, "ensure_running",
                        lambda: (True, ""))
    context = SimpleNamespace(preferences=SimpleNamespace(addons={}))
    from cloth_next.bake.controller import shared_controller
    shared_controller.reset()
    with pytest.raises(module.SceneValidationError):
        module.start_run(context)
    assert module._worker is None
    assert not module.run_active()
    snapshot = shared_controller.snapshot()
    assert snapshot.state.name == "ERROR"
    assert "bad material" in (snapshot.error_summary or "")
    shared_controller.reset()
    env.registration.unregister()


def test_run_plan_carries_fingerprint_and_material_meta(blender_env):
    env = blender_env
    env.registration.register()
    cloth_obj, cloth_settings = _settings(env)
    collider_obj, _ = _settings(env)
    env.object_properties.apply_preset(cloth_settings, "DENIM")
    cloth_settings.material.preset = "DENIM"
    shell, static, contact_enabled, preset = \
        env.solver_test._snapshot_materials(cloth_obj, collider_obj)
    from cloth_next.materials import formatting
    fingerprint = formatting.settings_fingerprint(shell, static,
                                                  contact_enabled, preset)
    assert preset == "DENIM"
    assert len(fingerprint) == 64
    env.registration.unregister()


# --- parameter inspection -------------------------------------------------------

def _scene_context(env, cloth_obj, collider_obj):
    scene = SimpleNamespace(
        objects=[cloth_obj, collider_obj], frame_start=1, frame_end=8,
        render=SimpleNamespace(fps=24), use_gravity=True,
        gravity=(0.0, 0.0, -9.81))
    return SimpleNamespace(object=cloth_obj, active_object=cloth_obj,
                           scene=scene)


def test_parameter_inspection_shows_artist_and_wire_names(blender_env):
    env = blender_env
    env.registration.register()
    cloth_obj, cloth_settings = _settings(env)
    collider_obj, collider_settings = _settings(env)
    cloth_settings.enabled = True
    collider_settings.enabled = True
    collider_settings.role = "COLLIDER"
    env.object_properties.apply_preset(cloth_settings, "COTTON")
    context = _scene_context(env, cloth_obj, collider_obj)
    lines, payload = env.solver_test.build_parameter_inspection(context)
    text = "\n".join(lines)
    assert "Stretch Resistance — PPF young-mod: 5500" in text
    assert "Maximum Stretch — PPF strain-limit: 0.05" in text
    assert "disable-contact: False" in text
    assert payload["group"][0][0]["young-mod"] == 5500.0
    assert payload["group"][0][0]["density"] == 1.0
    # JSON-safe and free of mesh data / secrets / binary blobs
    import json
    dumped = json.dumps(payload)
    assert "vert" not in dumped and "face" not in dumped
    cloth_settings.collision.enabled = False
    _lines, payload = env.solver_test.build_parameter_inspection(context)
    assert payload["scene"]["disable-contact"] is True
    env.registration.unregister()
