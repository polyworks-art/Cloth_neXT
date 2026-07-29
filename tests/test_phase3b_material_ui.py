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
            self.menus: list[tuple[str, str]] = []
            self.operators: list[tuple[str, str]] = []
            self.prop_texts: list[tuple[str, str]] = []
        self.use_property_split = False
        self.use_property_decorate = False
        self.enabled = True

    def prop(self, _data, name, **kw):
        self.sink.props.append(name)
        self.sink.prop_texts.append((name, kw.get("text", "")))

    def prop_search(self, _data, name, *_args, **_kw):
        self.sink.props.append(name)

    def label(self, text="", **_kw):
        self.sink.labels.append(text)

    def column(self, align=False):
        return RecordingLayout(self.sink)

    def row(self, align=False):
        return RecordingLayout(self.sink)

    def box(self):
        return RecordingLayout(self.sink)

    def operator(self, identifier, text="", **_kw):
        self.sink.operators.append((identifier, text))
        return SimpleNamespace()

    def menu(self, menu_id, text="", **_kw):
        self.sink.menus.append((menu_id, text))


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
                      "volume_conservation", "rest_shape",
                      "CLOTHNEXT_PG_quality_settings",
                      "CLOTHNEXT_PG_physical_settings",
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
    assert material.surface_weight == 1.0
    assert material.stretch_resistance == 1000.0
    assert material.sideways_response == 0.35
    assert material.bend_resistance == 10.0
    assert material.stretch_limit_enabled is False
    assert settings.damping.shape_damping == 0.0
    assert settings.collision.enabled is True
    assert settings.collision.surface_grip == 0.5
    assert settings.collision.collision_gap == 0.001
    assert settings.collision.surface_offset == 0.0
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
        props["surface_weight"]: ("kg/m²", "Technical PPF parameter: density"),
        props["stretch_resistance"]: ("density-normalized", "young-mod"),
        props["sideways_response"]: ("poiss-rat",),
        props["bend_resistance"]: ("bend",),
        props["stretch_limit_enabled"]: ("strain-limit",),
        props["maximum_stretch_percent"]: ("strain-limit",),
        props["model"]: ("Baraff-Witkin", "ARAP", "model"),
        dampings["shape_damping"]: ("seconds", "deformation-damping"),
        dampings["fold_damping"]: ("seconds", "bending-damping"),
        collisions["surface_grip"]: ("friction", "Minimum"),
        collisions["collision_gap"]: ("world units", "contact-gap"),
        collisions["surface_offset"]: ("world units", "contact-offset"),
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
    assert props["surface_weight"].keywords["min"] > 0.0
    assert props["surface_weight"].keywords["max"] == 10000.0
    assert props["maximum_stretch_percent"].keywords["max"] == 100.0
    assert props["maximum_stretch_percent"].keywords["min"] > 0.0


# --- preset behavior ----------------------------------------------------------

def test_preset_items_are_builtin_order_plus_custom(blender_env):
    items = blender_env.object_properties.PRESET_ITEMS
    identifiers = [item[0] for item in items]
    assert identifiers[:7] == ["DEFAULT_CLOTH", "SILK", "FLAG", "COTTON",
                              "WOOL", "DENIM", "LEATHER"]
    assert len(identifiers) == 76
    assert identifiers[-1] == "CUSTOM"
    assert items[-1][1] == "Custom"


def test_apply_preset_is_deterministic_and_exact(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    assert env.object_properties.apply_preset(settings, "SILK") is True
    material = settings.material
    assert material.model == "FABRIC"
    assert material.surface_weight == 1.0
    assert material.stretch_resistance == 500.0
    assert material.sideways_response == 0.4
    assert material.bend_resistance == 1.42
    assert material.stretch_limit_enabled is True
    assert material.maximum_stretch_percent == 6.0
    assert settings.collision.surface_grip == 0.25
    assert settings.damping.shape_damping == 0.0
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
    assert panel.layout.menus == [
        ("CLOTHNEXT_MT_material_presets", "Default Cloth")]
    env.registration.unregister()


# --- honest UI ----------------------------------------------------------------

def test_cloth_workflow_panels_are_registered(blender_env):
    ui = blender_env.physics_ui
    names = [cls.__name__ for cls in ui.CLASSES]
    for expected in ("CLOTHNEXT_PT_setup", "CLOTHNEXT_PT_simulation",
                     "CLOTHNEXT_PT_material", "CLOTHNEXT_PT_shape",
                     "CLOTHNEXT_PT_pressure", "CLOTHNEXT_PT_sewing",
                     "CLOTHNEXT_PT_collision",
                     "CLOTHNEXT_PT_cloth_advanced"):
        assert expected in names


def test_active_simulation_proxy_panel_exposes_simple_and_character_modes(
        blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "COLLIDER"
    settings.collider_motion = "ANIMATED"
    context = SimpleNamespace(
        object=obj, active_object=obj,
        scene=SimpleNamespace(objects=[obj]))

    settings.collider_proxy_type = "SIMPLE"
    panel = env.physics_ui.CLOTHNEXT_PT_simulation_proxy()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert panel.layout.props == [
        "collider_proxy_type", "collider_proxy_target_vertices"]
    assert ("clothnext.generate_collider_proxy", "Generate Simple Proxy")         in panel.layout.operators

    settings.collider_proxy_type = "CHARACTER_CAGE"
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert panel.layout.props == [
        "collider_proxy_type", "collider_cage_margin",
        "collider_cage_joint_overlap", "collider_cage_sample_step",
        "collider_cage_weight_threshold", "collider_cage_min_vertices"]
    assert "collider_proxy_target_vertices" not in panel.layout.props
    assert ("clothnext.generate_collider_proxy", "Generate Character Cage")         in panel.layout.operators
    env.registration.unregister()


def test_proxy_type_change_disables_the_other_generated_mode(blender_env):
    env = blender_env
    env.registration.register()
    _obj, settings = _settings(env)
    settings.collider_proxy_enabled = True
    env.object_properties._on_collider_proxy_type_update(settings, None)
    assert settings.collider_proxy_enabled is False
    env.registration.unregister()


def test_material_panel_displays_artist_facing_names(blender_env):
    env = blender_env
    env.registration.register()
    obj, _unused = _settings(env)
    panel = env.physics_ui.CLOTHNEXT_PT_material()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.props == ["model", "surface_weight",
                                  "stretch_resistance", "sideways_response",
                                  "bend_resistance",
                                  "stretch_limit_enabled",
                                  "maximum_stretch_percent",
                                  "shape_damping", "fold_damping"]
    assert "Stretch Protection" in panel.layout.labels
    assert "Damping" in panel.layout.labels
    assert "Permanent Deformation" in panel.layout.labels
    env.registration.unregister()


def test_cloth_main_panel_contains_only_object_type(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "CLOTH"
    panel = env.physics_ui.CLOTHNEXT_PT_physics()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.labels == ["Object Type"]
    assert panel.layout.operators == []
    assert not any("Version" in label or "Bake:" in label
                   for label in panel.layout.labels)
    env.registration.unregister()


def test_shape_uses_only_mapped_controls_and_inert_concepts(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    context = _context(obj)
    expected = {
        env.physics_ui.CLOTHNEXT_PT_pinning:
            ["pinning_enabled", "pin_group", "pin_mode"],
        env.physics_ui.CLOTHNEXT_PT_rest_shape: ["shrink_percent"],
        env.physics_ui.CLOTHNEXT_PT_pressure:
            ["enable_inflate", "inflate_pressure"],
        env.physics_ui.CLOTHNEXT_PT_sewing:
            ["sewing_enabled", "sewing_stiffness"],
    }
    for panel_type, props in expected.items():
        panel = panel_type()
        panel.layout = RecordingLayout()
        panel.draw(context)
        assert panel.layout.props == props
    shape = env.physics_ui.CLOTHNEXT_PT_shape()
    shape.layout = RecordingLayout()
    shape.draw(context)
    assert shape.layout.labels == [
        "Advanced Pin Motion", "◇", "Soft Constraints", "◇"]
    assert shape.layout.props == []
    assert shape.layout.operators == []
    env.registration.unregister()


def test_remove_action_is_only_in_cloth_maintenance(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    context = _context(obj)
    maintenance = env.physics_ui.CLOTHNEXT_PT_maintenance()
    maintenance.layout = RecordingLayout()
    maintenance.draw(context)
    assert maintenance.layout.operators == [
        ("clothnext.remove_physics", "Remove Cloth NeXt")]
    env.registration.unregister()


def test_soft_body_uses_role_specific_material_shape_and_collision(
        blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "SOFT_BODY"
    context = _context(obj)

    material = env.physics_ui.CLOTHNEXT_PT_material()
    material.layout = RecordingLayout()
    material.draw(context)
    assert material.layout.props == [
        "volume_density", "stretch_resistance", "poisson_ratio",
        "volume_scale", "tetrahedralizer", "shape_damping"]
    assert ("poisson_ratio", "Sideways Response") in material.layout.prop_texts
    assert material.layout.labels == [
        "Solver Model", "ARAP", "Damping",
        "Permanent Deformation", "◇"]

    shape = env.physics_ui.CLOTHNEXT_PT_shape()
    shape.layout = RecordingLayout()
    shape.draw(context)
    assert shape.layout.labels == ["Soft Constraints", "◇"]
    assert shape.layout.props == []
    assert shape.layout.operators == []

    rest = env.physics_ui.CLOTHNEXT_PT_soft_body_rest_shape()
    rest.layout = RecordingLayout()
    rest.draw(context)
    assert rest.layout.props == ["volume_scale"]
    assert ("volume_scale", "Uniform Scale") in rest.layout.prop_texts

    collision = env.physics_ui.CLOTHNEXT_PT_collision()
    collision.layout = RecordingLayout()
    collision.draw(context)
    assert collision.layout.props == [
        "enabled", "surface_grip", "collision_gap", "surface_offset"]
    assert collision.layout.labels == [
        "Collision Timing", "◇",
        "Advanced Contact Distance", "◇"]
    env.registration.unregister()


def test_soft_body_workflow_visibility_excludes_role_specific_panels(
        blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "SOFT_BODY"
    context = _context(obj)
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_setup,
            env.physics_ui.CLOTHNEXT_PT_simulation,
            env.physics_ui.CLOTHNEXT_PT_material,
            env.physics_ui.CLOTHNEXT_PT_shape,
            env.physics_ui.CLOTHNEXT_PT_collision,
            env.physics_ui.CLOTHNEXT_PT_cloth_advanced):
        assert panel.poll(context), panel.__name__
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_pinning,
            env.physics_ui.CLOTHNEXT_PT_pressure,
            env.physics_ui.CLOTHNEXT_PT_sewing,
            env.physics_ui.CLOTHNEXT_PT_friction_regions,
            env.physics_ui.CLOTHNEXT_PT_collisions,
            env.physics_ui.CLOTHNEXT_PT_cache,
            env.physics_ui.CLOTHNEXT_PT_advanced):
        assert not panel.poll(context), panel.__name__
    env.registration.unregister()


def test_soft_body_main_panel_contains_only_object_type(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "SOFT_BODY"
    panel = env.physics_ui.CLOTHNEXT_PT_physics()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.labels == ["Object Type"]
    assert panel.layout.operators == []
    env.registration.unregister()


def test_rigid_body_uses_only_mapped_material_and_shared_collision(
        blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "RIGID_BODY"
    context = _context(obj)

    material = env.physics_ui.CLOTHNEXT_PT_material()
    material.layout = RecordingLayout()
    material.draw(context)
    assert material.layout.props == ["volume_density"]
    assert material.layout.prop_texts == [
        ("volume_density", "Volume Density")]
    assert material.layout.labels == []
    assert material.layout.operators == []

    collision = env.physics_ui.CLOTHNEXT_PT_collision()
    collision.layout = RecordingLayout()
    collision.draw(context)
    assert collision.layout.props == [
        "enabled", "surface_grip", "collision_gap", "surface_offset"]
    assert collision.layout.labels == [
        "Collision Timing", "◇",
        "Advanced Contact Distance", "◇"]
    assert collision.layout.operators == []

    advanced = env.physics_ui.CLOTHNEXT_PT_cloth_advanced()
    advanced.layout = RecordingLayout()
    advanced.draw(context)
    assert advanced.layout.labels == [
        "Motion Overrides", "◇",
        "Advanced Contact Solver", "◇"]
    assert advanced.layout.props == []
    assert advanced.layout.operators == []
    env.registration.unregister()


def test_rigid_body_workflow_has_no_shape_or_role_specific_panels(
        blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "RIGID_BODY"
    context = _context(obj)
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_setup,
            env.physics_ui.CLOTHNEXT_PT_simulation,
            env.physics_ui.CLOTHNEXT_PT_material,
            env.physics_ui.CLOTHNEXT_PT_collision,
            env.physics_ui.CLOTHNEXT_PT_cloth_advanced):
        assert panel.poll(context), panel.__name__
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_shape,
            env.physics_ui.CLOTHNEXT_PT_pinning,
            env.physics_ui.CLOTHNEXT_PT_soft_body_rest_shape,
            env.physics_ui.CLOTHNEXT_PT_pressure,
            env.physics_ui.CLOTHNEXT_PT_sewing,
            env.physics_ui.CLOTHNEXT_PT_friction_regions,
            env.physics_ui.CLOTHNEXT_PT_collisions,
            env.physics_ui.CLOTHNEXT_PT_cache,
            env.physics_ui.CLOTHNEXT_PT_advanced):
        assert not panel.poll(context), panel.__name__
    env.registration.unregister()


def test_rigid_body_main_panel_contains_only_object_type(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "RIGID_BODY"
    panel = env.physics_ui.CLOTHNEXT_PT_physics()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.labels == ["Object Type"]
    assert panel.layout.operators == []
    env.registration.unregister()


def test_cable_rope_keeps_internal_rod_role_and_visible_name(blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Cable", type="CURVE")
    settings = obj.cloth_next
    settings.enabled = True
    settings.role = "ROD"
    panel = env.physics_ui.CLOTHNEXT_PT_physics()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert settings.role == "ROD"
    assert panel.layout.labels == ["Object Type"]
    assert panel.layout.menus == [
        ("CLOTHNEXT_MT_object_type", "Cable / Rope")]
    assert panel.layout.operators == []
    env.registration.unregister()


def test_cable_rope_uses_only_mapped_material_shape_and_collision(
        blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Cable", type="CURVE")
    settings = obj.cloth_next
    settings.enabled = True
    settings.role = "ROD"
    context = _context(obj)

    material = env.physics_ui.CLOTHNEXT_PT_material()
    material.layout = RecordingLayout()
    material.draw(context)
    assert material.layout.props == [
        "linear_density", "stretch_resistance", "bend_resistance",
        "stretch_limit_percent", "shape_damping", "fold_damping"]
    assert ("stretch_limit_percent", "Maximum Stretch") in \
        material.layout.prop_texts
    assert ("fold_damping", "Bend Damping") in material.layout.prop_texts
    assert material.layout.labels == ["Stretch Protection", "Damping"]

    shape = env.physics_ui.CLOTHNEXT_PT_shape()
    shape.layout = RecordingLayout()
    shape.draw(context)
    assert shape.layout.labels == [
        "Pinning", "◇",
        "Advanced Pin Motion", "◇",
        "Soft Constraints", "◇"]
    assert shape.layout.props == []
    assert shape.layout.operators == []

    rest = env.physics_ui.CLOTHNEXT_PT_cable_rope_rest_shape()
    rest.layout = RecordingLayout()
    rest.draw(context)
    assert rest.layout.props == ["length_factor"]
    assert ("length_factor", "Length Scale") in rest.layout.prop_texts

    collision = env.physics_ui.CLOTHNEXT_PT_collision()
    collision.layout = RecordingLayout()
    collision.draw(context)
    assert collision.layout.props == [
        "enabled", "surface_grip", "surface_offset", "collision_gap"]
    assert ("surface_offset", "Collision Radius") in \
        collision.layout.prop_texts
    assert collision.layout.labels == [
        "Collision Timing", "◇",
        "Advanced Contact Distance", "◇"]
    assert collision.layout.operators == []
    env.registration.unregister()


def test_cable_rope_workflow_excludes_unavailable_and_foreign_panels(
        blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Cable", type="CURVE")
    settings = obj.cloth_next
    settings.enabled = True
    settings.role = "ROD"
    context = _context(obj)
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_setup,
            env.physics_ui.CLOTHNEXT_PT_simulation,
            env.physics_ui.CLOTHNEXT_PT_material,
            env.physics_ui.CLOTHNEXT_PT_shape,
            env.physics_ui.CLOTHNEXT_PT_collision,
            env.physics_ui.CLOTHNEXT_PT_cloth_advanced,
            env.physics_ui.CLOTHNEXT_PT_cable_rope_rest_shape):
        assert panel.poll(context), panel.__name__
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_pinning,
            env.physics_ui.CLOTHNEXT_PT_rest_shape,
            env.physics_ui.CLOTHNEXT_PT_soft_body_rest_shape,
            env.physics_ui.CLOTHNEXT_PT_pressure,
            env.physics_ui.CLOTHNEXT_PT_sewing,
            env.physics_ui.CLOTHNEXT_PT_friction_regions,
            env.physics_ui.CLOTHNEXT_PT_damping,
            env.physics_ui.CLOTHNEXT_PT_collisions,
            env.physics_ui.CLOTHNEXT_PT_cache,
            env.physics_ui.CLOTHNEXT_PT_advanced):
        assert not panel.poll(context), panel.__name__
    env.registration.unregister()


def test_cache_panel_shows_editable_bake_range(blender_env):
    env = blender_env
    env.registration.register()
    obj, _unused = _settings(env)
    panel = env.physics_ui.CLOTHNEXT_PT_cache()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert "bake_start" in panel.layout.props
    assert "bake_end" in panel.layout.props
    assert "cache_directory" in panel.layout.props
    assert "frame_start" not in panel.layout.props
    assert "frame_end" not in panel.layout.props
    env.registration.unregister()


def test_rigid_body_result_replaces_legacy_cache_panel(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "RIGID_BODY"
    context = _context(obj)
    assert not env.physics_ui.CLOTHNEXT_PT_cache.poll(context)
    assert env.physics_ui.CLOTHNEXT_PT_result.poll(context)
    env.registration.unregister()


def test_collider_collision_motion_samples_and_contact_values(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.role = "COLLIDER"
    settings.enabled = True
    panel = env.physics_ui.CLOTHNEXT_PT_collider_collision()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.props == ["collider_motion", "surface_grip", "collision_gap",
                                  "surface_offset"]
    settings.collider_motion = "ANIMATED"
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.props[:3] == [
        "collider_motion", "collider_capture_mode",
        "collider_samples_per_frame"]
    assert panel.layout.props == [
        "collider_motion", "collider_capture_mode",
        "collider_samples_per_frame",
        "surface_grip", "collision_gap", "surface_offset"]
    assert ("collider_samples_per_frame", "Samples per Frame") in \
        panel.layout.prop_texts
    settings.collider_motion = "STATIC"
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert "collider_samples_per_frame" not in panel.layout.props
    env.registration.unregister()


def test_collider_proxy_panel_uses_cached_estimates_only(
        blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "COLLIDER"
    settings.collider_motion = "ANIMATED"
    settings.collider_proxy_source_vertices = 184_320
    settings.collider_proxy_result_vertices = 8_000
    proxy = env.bpy.types.Object(name="Proxy", type="MESH")
    settings.collider_proxy_object = proxy
    settings.collider_proxy_enabled = True
    monkeypatch.setattr(
        env.collider_proxy, "proxy_estimate",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("draw must not inspect proxy meshes")))
    context = SimpleNamespace(
        object=obj, active_object=obj,
        scene=SimpleNamespace(objects=[obj]))
    panel = env.physics_ui.CLOTHNEXT_PT_simulation_proxy()
    panel.layout = RecordingLayout()
    panel.draw(context)
    assert panel.layout.props == [
        "collider_proxy_type", "collider_proxy_target_vertices",
        "collider_proxy_enabled"]
    assert panel.layout.operators == [
        ("clothnext.generate_collider_proxy", "Regenerate Simple Proxy")]
    assert "Source: 184,320 vertices" in panel.layout.labels
    assert "Proxy: 8,000 vertices" in panel.layout.labels
    assert "Estimated Peak Memory" in panel.layout.labels
    env.registration.unregister()


def test_collider_workflow_visibility_and_compact_advanced(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    settings.role = "COLLIDER"
    context = _context(obj)
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_setup,
            env.physics_ui.CLOTHNEXT_PT_simulation,
            env.physics_ui.CLOTHNEXT_PT_collider_collision,
            env.physics_ui.CLOTHNEXT_PT_cloth_advanced,
            env.physics_ui.CLOTHNEXT_PT_solver_settings,
            env.physics_ui.CLOTHNEXT_PT_simulation_engine,
            env.physics_ui.CLOTHNEXT_PT_diagnostics,
            env.physics_ui.CLOTHNEXT_PT_maintenance):
        assert panel.poll(context), panel.__name__
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_material,
            env.physics_ui.CLOTHNEXT_PT_shape,
            env.physics_ui.CLOTHNEXT_PT_collision,
            env.physics_ui.CLOTHNEXT_PT_result,
            env.physics_ui.CLOTHNEXT_PT_friction_regions,
            env.physics_ui.CLOTHNEXT_PT_collisions,
            env.physics_ui.CLOTHNEXT_PT_advanced):
        assert not panel.poll(context), panel.__name__
    advanced = env.physics_ui.CLOTHNEXT_PT_cloth_advanced()
    advanced.layout = RecordingLayout()
    advanced.draw(context)
    assert advanced.layout.labels == [
        "Advanced Contact Solver", "◇"]
    assert advanced.layout.props == []
    assert advanced.layout.operators == []
    diagnostics = env.physics_ui.CLOTHNEXT_PT_diagnostics()
    diagnostics.layout = RecordingLayout()
    diagnostics.draw(context)
    assert diagnostics.layout.operators == [
        ("clothnext.companion_launch", "Open Bake Window"),
        ("clothnext.companion_open_logs", "Open Logs")]
    env.registration.unregister()


def test_force_setup_switches_only_between_mapped_controls(blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Force", type="EMPTY")
    settings = obj.cloth_next
    settings.enabled = True
    settings.role = "FORCE"
    panel = env.physics_ui.CLOTHNEXT_PT_setup()
    expected = {
        "GRAVITY": (
            ["force_type", "strength"], ["Direction: Local -Z"], None),
        "WIND": (
            ["force_type", "strength"], ["Direction: Local +Z"], None),
        "AIR_DENSITY": (
            ["force_type", "air_density"], [], ("air_density", "Density")),
        "AIR_FRICTION": (
            ["force_type", "air_friction"], [], ("air_friction", "Friction")),
        "VERTEX_AIR_DAMP": (
            ["force_type", "vertex_air_damp"], [],
            ("vertex_air_damp", "Damping")),
    }
    for force_type, (props, labels, renamed_prop) in expected.items():
        settings.force.force_type = force_type
        panel.layout = RecordingLayout()
        panel.draw(_context(obj))
        assert panel.layout.props == props
        assert panel.layout.labels == labels
        assert panel.layout.operators == []
        assert "wind_variation" not in panel.layout.props
        if renamed_prop is not None:
            assert renamed_prop in panel.layout.prop_texts
    env.registration.unregister()


def test_force_workflow_has_only_setup_simulation_and_compact_advanced(
        blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Wind", type="EMPTY")
    settings = obj.cloth_next
    settings.enabled = True
    settings.role = "FORCE"
    context = _context(obj)
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_setup,
            env.physics_ui.CLOTHNEXT_PT_simulation,
            env.physics_ui.CLOTHNEXT_PT_cloth_advanced,
            env.physics_ui.CLOTHNEXT_PT_solver_settings,
            env.physics_ui.CLOTHNEXT_PT_simulation_engine,
            env.physics_ui.CLOTHNEXT_PT_diagnostics,
            env.physics_ui.CLOTHNEXT_PT_maintenance):
        assert panel.poll(context), panel.__name__
    for panel in (
            env.physics_ui.CLOTHNEXT_PT_force,
            env.physics_ui.CLOTHNEXT_PT_material,
            env.physics_ui.CLOTHNEXT_PT_shape,
            env.physics_ui.CLOTHNEXT_PT_collision,
            env.physics_ui.CLOTHNEXT_PT_collider_collision,
            env.physics_ui.CLOTHNEXT_PT_simulation_proxy,
            env.physics_ui.CLOTHNEXT_PT_result,
            env.physics_ui.CLOTHNEXT_PT_cache,
            env.physics_ui.CLOTHNEXT_PT_advanced):
        assert not panel.poll(context), panel.__name__
    advanced = env.physics_ui.CLOTHNEXT_PT_cloth_advanced()
    advanced.layout = RecordingLayout()
    advanced.draw(context)
    assert advanced.layout.labels == []
    assert advanced.layout.props == []
    assert advanced.layout.operators == []
    diagnostics = env.physics_ui.CLOTHNEXT_PT_diagnostics()
    diagnostics.layout = RecordingLayout()
    diagnostics.draw(context)
    assert diagnostics.layout.operators == [
        ("clothnext.companion_launch", "Open Bake Window"),
        ("clothnext.companion_open_logs", "Open Logs")]
    assert "DEFAULT_CLOSED" in \
        env.physics_ui.CLOTHNEXT_PT_cloth_advanced.bl_options
    env.registration.unregister()


def test_force_main_panel_contains_only_object_type(blender_env):
    env = blender_env
    env.registration.register()
    obj = env.bpy.types.Object(name="Wind", type="EMPTY")
    obj.cloth_next.enabled = True
    obj.cloth_next.role = "FORCE"
    panel = env.physics_ui.CLOTHNEXT_PT_physics()
    panel.layout = RecordingLayout()
    panel.draw(_context(obj))
    assert panel.layout.labels == ["Object Type"]
    assert panel.layout.menus == [("CLOTHNEXT_MT_object_type", "Force")]
    assert panel.layout.operators == []
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
                      "self_collision", "rest_scale",
                      "volume_conservation", '"frame_start"',
                      '"frame_end"'):
        assert forbidden not in source, forbidden


def test_role_workflow_panels_use_existing_semantic_header_icons(blender_env):
    env = blender_env
    expected = {
        env.physics_ui.CLOTHNEXT_PT_setup: "setup",
        env.physics_ui.CLOTHNEXT_PT_simulation: "solver",
        env.physics_ui.CLOTHNEXT_PT_material: "physical",
        env.physics_ui.CLOTHNEXT_PT_shape: "shape",
        env.physics_ui.CLOTHNEXT_PT_rest_shape: "rest_shape",
        env.physics_ui.CLOTHNEXT_PT_soft_body_rest_shape: "rest_shape",
        env.physics_ui.CLOTHNEXT_PT_cable_rope_rest_shape: "rest_shape",
        env.physics_ui.CLOTHNEXT_PT_pressure: "pressure",
        env.physics_ui.CLOTHNEXT_PT_sewing: "sewing",
        env.physics_ui.CLOTHNEXT_PT_collision: "collision",
        env.physics_ui.CLOTHNEXT_PT_friction_regions: "friction_regions",
        env.physics_ui.CLOTHNEXT_PT_collider_collision: "collision",
        env.physics_ui.CLOTHNEXT_PT_simulation_proxy: "simulation_proxy",
        env.physics_ui.CLOTHNEXT_PT_cloth_advanced: "advanced",
        env.physics_ui.CLOTHNEXT_PT_solver_settings: "solver_settings",
        env.physics_ui.CLOTHNEXT_PT_simulation_engine: "engine",
        env.physics_ui.CLOTHNEXT_PT_result: "result",
        env.physics_ui.CLOTHNEXT_PT_diagnostics: "diagnostics",
        env.physics_ui.CLOTHNEXT_PT_maintenance: "maintenance",
    }
    available = set(env.physics_ui.icon_registry._NAMES)
    for panel, icon in expected.items():
        assert panel.header_icon == icon, panel.__name__
        assert icon in available, panel.__name__
        assert icon != "info", panel.__name__


def test_quality_preset_operator_uses_button_specific_hover_tooltip(
        blender_env):
    operators = blender_env.physics_operators
    classes = (
        operators.CLOTHNEXT_OT_apply_quality_low,
        operators.CLOTHNEXT_OT_apply_quality_medium,
        operators.CLOTHNEXT_OT_apply_quality_high,
        operators.CLOTHNEXT_OT_apply_quality_extreme,
    )
    assert [operator.quality_preset for operator in classes] == [
        "LOW", "MEDIUM", "HIGH", "EXTREME"]
    assert [operators.QUALITY_PRESET_OPERATOR_IDS[preset]
            for preset in ("LOW", "MEDIUM", "HIGH", "EXTREME")] == [
        operator.bl_idname for operator in classes]
    assert classes[0].bl_description.startswith("Fast previews")
    assert "increase simulation time" in classes[-1].bl_description
    mixed_classes = (
        operators.CLOTHNEXT_OT_apply_quality_xmedium,
        operators.CLOTHNEXT_OT_apply_quality_xhigh,
        operators.CLOTHNEXT_OT_apply_quality_xextreme,
    )
    assert [operator.quality_preset for operator in mixed_classes] == [
        "MEDIUM", "HIGH", "EXTREME"]
    assert [operators.PDRD_QUALITY_PRESET_OPERATOR_IDS[preset]
            for preset in ("MEDIUM", "HIGH", "EXTREME")] == [
        operator.bl_idname for operator in mixed_classes]
    assert mixed_classes[0].bl_description.startswith("Fast, stable")
    assert "increase simulation time" in mixed_classes[-1].bl_description


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
    from cloth_next.materials import ShellMaterialSettings, StaticMaterialSettings
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


def test_invalid_material_precedes_solver_resolution_process(blender_env,
                                                              monkeypatch):
    env = blender_env
    env.registration.register()
    cloth_obj, cloth_settings = _settings(env)
    collider_obj, collider_settings = _settings(env)
    cloth_settings.enabled = collider_settings.enabled = True
    collider_settings.role = "COLLIDER"
    cloth_settings.material.sideways_response = 0.75
    context = _scene_context(env, cloth_obj, collider_obj)
    monkeypatch.setattr(env.solver_test, "resolve_solver",
                        lambda _context: (_ for _ in ()).throw(
                            AssertionError("solver process probed first")))
    with pytest.raises(env.solver_test.SceneValidationError):
        env.solver_test.build_run_plan(context)
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


def test_material_library_is_a_hover_category_menu(blender_env):
    env = blender_env
    env.registration.register()
    main = env.physics_ui.CLOTHNEXT_MT_material_presets()
    main.layout = RecordingLayout()
    main.draw(SimpleNamespace())
    assert [text for _menu_id, text in main.layout.menus] == list(
        material_presets.CATEGORY_LABELS.values())
    category = next(menu for menu in env.physics_ui.MATERIAL_PRESET_CATEGORY_MENUS
                    if menu.category == "LIGHTWEIGHT")()
    category.layout = RecordingLayout()
    obj, _settings_unused = _settings(env)
    category.draw(_context(obj))
    assert len(material_presets.presets_in_category("LIGHTWEIGHT")) == 5
    env.registration.unregister()


def test_material_preset_operator_applies_and_selects(blender_env):
    env = blender_env
    env.registration.register()
    obj, settings = _settings(env)
    settings.enabled = True
    operator = env.physics_operators.CLOTHNEXT_OT_apply_material_preset()
    operator.preset = "MIT_NYLON_RIPSTOP"
    assert operator.execute(_context(obj)) == {"FINISHED"}
    assert settings.material.preset == "MIT_NYLON_RIPSTOP"
    assert settings.material.surface_weight == pytest.approx(0.058516)
    env.registration.unregister()


def test_parameter_inspection_allows_no_collider(blender_env):
    env=blender_env; env.registration.register()
    cloth_obj,cloth_settings=_settings(env)
    cloth_settings.enabled=True
    context=_scene_context(env,cloth_obj,cloth_obj)
    context.scene.objects=[cloth_obj]
    lines,payload=env.solver_test.build_parameter_inspection(context)
    assert "Colliders: None (optional)" in lines
    assert len(payload["group"])==2
    assert payload["group"][1][2]==["cloth-next-internal-static-v1"]
    env.registration.unregister()
