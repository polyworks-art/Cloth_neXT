# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Phase 2.8A acceptance tests: Physics Properties integration.

Runs against the lightweight fake ``bpy`` from ``tests/fake_bpy.py``; the
real-Blender behavior is covered by ``tools/blender_smoke_test.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import fake_bpy

REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_PACKAGE = REPO_ROOT / "cloth_next" / "blender"


def make_context(obj):
    return SimpleNamespace(active_object=obj, object=obj)


def make_mesh(env, name="ClothMesh"):
    return env.bpy.types.Object(name=name, type="MESH")


def draw_funcs(env):
    return env.bpy.types.PHYSICS_PT_add.draw._draw_funcs


# --- 1: registration contains the Phase 2.8 classes ----------------------------

def test_registration_registers_phase28_classes_in_order(blender_env):
    env = blender_env
    env.registration.register()
    names = [cls.__name__ for cls in env.bpy.registry]
    assert "CLOTHNEXT_PG_object_settings" in names
    assert "CLOTHNEXT_OT_add_physics" in names
    assert "CLOTHNEXT_OT_remove_physics" in names
    assert "CLOTHNEXT_PT_physics" in names
    # operators register before panels
    assert names.index("CLOTHNEXT_OT_add_physics") < names.index("CLOTHNEXT_PT_physics")
    # PropertyGroup registered and PointerProperty attached afterwards
    assert names.index("CLOTHNEXT_PG_object_settings") < len(names)
    assert hasattr(env.bpy.types.Object, "cloth_next")
    env.registration.unregister()


# --- 2: role enum ----------------------------------------------------------------

def test_object_settings_define_cloth_and_collider_roles(blender_env):
    env = blender_env
    props = fake_bpy._resolved_props(env.object_properties.CLOTHNEXT_PG_object_settings)
    role = props["role"]
    items = role.keywords["items"]
    identifiers = [item[0] for item in items]
    labels = {item[0]: item[1] for item in items}
    assert identifiers == ["CLOTH", "ROD", "SOFT_BODY", "COLLIDER", "FORCE"]
    assert labels == {"CLOTH": "Cloth", "ROD": "Rod / Cable",
                      "SOFT_BODY": "Soft Body", "COLLIDER": "Collider",
                      "FORCE": "Force"}
    assert role.keywords["default"] == "CLOTH"
    assert props["enabled"].keywords["default"] is False


def test_object_type_menu_does_not_create_duplicate_stored_state(blender_env):
    env = blender_env
    props = fake_bpy._resolved_props(env.object_properties.CLOTHNEXT_PG_object_settings)
    assert "role" in props
    assert not ({"object_type", "selected_object_type", "menu_role"} & props.keys())


@pytest.mark.parametrize("role", ["CLOTH", "COLLIDER"])
def test_supported_object_types_set_authoritative_role(blender_env, role):
    env = blender_env
    env.registration.register()
    obj = make_mesh(env)
    obj.cloth_next.enabled = True
    context = make_context(obj)
    operator = env.physics_operators.CLOTHNEXT_OT_set_object_type()
    operator.role = role
    assert operator.execute(context) == {"FINISHED"}
    assert obj.cloth_next.role == role
    env.registration.unregister()


def test_force_role_is_empty_only_and_add_defaults_empty_to_force(blender_env):
    env = blender_env
    env.registration.register()
    empty = env.bpy.types.Object(name="Wind", type="EMPTY")
    context = make_context(empty)
    add = env.physics_operators.CLOTHNEXT_OT_add_physics()
    assert add.execute(context) == {"FINISHED"}
    assert empty.cloth_next.role == "FORCE"
    operator = env.physics_operators.CLOTHNEXT_OT_set_object_type()
    operator.role = "FORCE"
    assert operator.execute(context) == {"FINISHED"}
    mesh = make_mesh(env)
    mesh.cloth_next.enabled = True
    assert operator.execute(make_context(mesh)) == {"CANCELLED"}
    env.registration.unregister()


def test_force_settings_expose_every_dynamic_ppf_environment_parameter(blender_env):
    props = fake_bpy._resolved_props(
        blender_env.object_properties.CLOTHNEXT_PG_force_settings)
    assert [item[0] for item in props["force_type"].keywords["items"]] == [
        "GRAVITY", "WIND", "AIR_DENSITY", "AIR_FRICTION",
        "VERTEX_AIR_DAMP"]
    assert props["air_density"].keywords["default"] == 0.001
    assert props["air_friction"].keywords["default"] == 0.2
    assert props["vertex_air_damp"].keywords["default"] == 0.0
    for name in ("strength", "air_density", "air_friction",
                 "vertex_air_damp"):
        assert "SKIP_SAVE" not in props[name].keywords.get("options", set())


def test_empty_data_panel_provides_force_enable_entry(blender_env):
    env = blender_env
    env.registration.register()
    empty = env.bpy.types.Object(name="Force Empty", type="EMPTY")
    context = make_context(empty)
    context.object = empty
    panel_type = env.physics_ui.CLOTHNEXT_PT_empty_force
    assert panel_type.poll(context)

    class Layout:
        def __init__(self):
            self.operators = []
        def operator(self, identifier, **kwargs):
            self.operators.append((identifier, kwargs))
            return SimpleNamespace()
        def label(self, **_kwargs):
            pass

    panel = panel_type()
    panel.layout = Layout()
    panel.draw(context)
    assert panel.layout.operators == [(
        "clothnext.add_physics",
        {"text": "Enable Cloth NeXt Force", "icon": "FORCE_FORCE"})]
    env.registration.unregister()


@pytest.mark.parametrize("role", ["ROPE_CABLE", "RIGID_BODY", "SAND"])
def test_unsupported_object_types_cannot_change_stored_role(blender_env, role):
    env = blender_env
    env.registration.register()
    obj = make_mesh(env)
    obj.cloth_next.enabled = True
    obj.cloth_next.role = "COLLIDER"
    operator = env.physics_operators.CLOTHNEXT_OT_set_object_type()
    operator.role = role
    assert operator.execute(make_context(obj)) == {"CANCELLED"}
    assert obj.cloth_next.role == "COLLIDER"
    env.registration.unregister()


def test_unavailable_menu_rows_are_disabled_alerts_with_coming_soon_tooltips(blender_env):
    env = blender_env

    class Layout:
        def __init__(self):
            self.rows = []
        def operator(self, *_args, **kwargs):
            op = SimpleNamespace(kwargs=kwargs)
            self.operator_value = op
            return op
        def separator(self):
            pass
        def row(self):
            row = Layout()
            row.alert = False
            row.enabled = True
            self.rows.append(row)
            return row

    menu = env.physics_ui.CLOTHNEXT_MT_object_type()
    menu.layout = Layout()
    menu.draw(None)
    unavailable = menu.layout.rows[-2:]
    assert all(row.alert and not row.enabled for row in unavailable)
    assert all(row.operator_value.kwargs["icon"] == "LOCKED" for row in unavailable)
    tooltips = [row.operator_value.tooltip for row in unavailable]
    assert all(tooltip.startswith("Coming soon.") for tooltip in tooltips)
    assert {"PPF PDRD", "Granular"} == {
        next(word for word in ("Volumetric", "PPF Rod", "PPF PDRD", "Granular")
             if word in tooltip) for tooltip in tooltips}


def test_existing_role_enum_identifiers_remain_blend_compatible(blender_env):
    role = fake_bpy._resolved_props(
        blender_env.object_properties.CLOTHNEXT_PG_object_settings)["role"]
    items = role.keywords["items"]
    assert tuple(item[0] for item in items) == (
        "CLOTH", "ROD", "SOFT_BODY", "COLLIDER", "FORCE")


def test_object_type_menu_uses_distinct_role_icons(blender_env):
    env = blender_env

    class Layout:
        def __init__(self, operators=None):
            self.operators = [] if operators is None else operators
        def operator(self, *_args, **kwargs):
            operator = SimpleNamespace(kwargs=kwargs)
            self.operators.append(operator)
            return operator
        def separator(self):
            pass
        def row(self):
            row = Layout(self.operators)
            row.alert = False
            row.enabled = True
            return row

    menu = env.physics_ui.CLOTHNEXT_MT_object_type()
    menu.layout = Layout()
    context = SimpleNamespace(object=SimpleNamespace(
        cloth_next=SimpleNamespace(role="ROD")))
    menu.draw(context)
    active = menu.layout.operators[:5]
    assert [item.kwargs["icon"] for item in active] == [
        "MOD_CLOTH", "CURVE_DATA", "MOD_SOFT", "MESH_CUBE", "FORCE_FORCE"]
    assert [item.kwargs["depress"] for item in active] == [
        False, True, False, False, False]


# --- 3+4: add operator ------------------------------------------------------------

def test_add_operator_rejects_missing_and_non_mesh_objects(blender_env):
    env = blender_env
    env.registration.register()
    add = env.physics_operators.CLOTHNEXT_OT_add_physics
    assert add.poll(make_context(None)) is False
    camera = env.bpy.types.Object(name="Camera", type="CAMERA")
    assert add.poll(make_context(camera)) is False
    env.registration.unregister()


def test_add_operator_enables_cloth_next_with_cloth_default(blender_env):
    env = blender_env
    env.registration.register()
    obj = make_mesh(env)
    context = make_context(obj)
    add_cls = env.physics_operators.CLOTHNEXT_OT_add_physics
    assert add_cls.poll(context) is True
    operator = add_cls()
    assert operator.execute(context) == {"FINISHED"}
    assert obj.cloth_next.enabled is True
    assert obj.cloth_next.role == "CLOTH"
    assert any("INFO" in levels for levels, _msg in operator.reports)
    # already enabled: the add operator is no longer available
    assert add_cls.poll(context) is False
    env.registration.unregister()


# --- 5: remove operator ------------------------------------------------------------

def test_remove_operator_resets_only_cloth_next_state(blender_env):
    env = blender_env
    env.registration.register()
    obj = make_mesh(env)
    obj.modifiers.new("Subsurf", "SUBSURF")
    context = make_context(obj)
    env.physics_operators.CLOTHNEXT_OT_add_physics().execute(context)
    obj.cloth_next.role = "COLLIDER"

    remove_cls = env.physics_operators.CLOTHNEXT_OT_remove_physics
    assert remove_cls.poll(context) is True
    operator = remove_cls()
    assert operator.execute(context) == {"FINISHED"}
    assert obj.cloth_next.enabled is False
    assert obj.cloth_next.role == "CLOTH"
    # unrelated data untouched
    assert [m.type for m in obj.modifiers] == ["SUBSURF"]
    # not enabled anymore: remove no longer available
    assert remove_cls.poll(context) is False
    env.registration.unregister()


def test_remove_operator_unavailable_without_cloth_next(blender_env):
    env = blender_env
    env.registration.register()
    obj = make_mesh(env)
    assert env.physics_operators.CLOTHNEXT_OT_remove_physics.poll(make_context(obj)) is False
    assert env.physics_operators.CLOTHNEXT_OT_remove_physics.poll(make_context(None)) is False
    env.registration.unregister()


# --- 6: no native Cloth modifier ---------------------------------------------------

def test_add_operator_creates_no_native_cloth_modifier(blender_env):
    env = blender_env
    env.registration.register()
    obj = make_mesh(env)
    env.physics_operators.CLOTHNEXT_OT_add_physics().execute(make_context(obj))
    assert list(obj.modifiers) == []
    env.registration.unregister()


def test_blender_modules_never_call_modifiers_new():
    for path in BLENDER_PACKAGE.glob("*.py"):
        assert "modifiers.new" not in path.read_text(encoding="utf-8"), path


# --- 7+8: UI placement, no N-panel -------------------------------------------------

def test_panel_lives_in_physics_properties_context(blender_env):
    panel = blender_env.physics_ui.CLOTHNEXT_PT_physics
    assert panel.bl_space_type == "PROPERTIES"
    assert panel.bl_region_type == "WINDOW"
    assert panel.bl_context == "physics"


def test_panel_poll_requires_enabled_mesh(blender_env):
    env = blender_env
    env.registration.register()
    panel = env.physics_ui.CLOTHNEXT_PT_physics
    assert panel.poll(make_context(None)) is False
    camera = env.bpy.types.Object(name="Camera", type="CAMERA")
    assert panel.poll(make_context(camera)) is False
    obj = make_mesh(env)
    assert panel.poll(make_context(obj)) is False
    obj.cloth_next.enabled = True
    assert panel.poll(make_context(obj)) is True
    env.registration.unregister()


def test_no_n_panel_is_introduced():
    for path in BLENDER_PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert 'bl_region_type = "UI"' not in source, path
        assert "bl_category" not in source, path
        assert 'bl_space_type = "VIEW_3D"' not in source, path


# --- 9: draw callback appended and removed exactly once ----------------------------

def test_draw_callback_appended_and_removed_exactly_once(blender_env):
    env = blender_env
    assert draw_funcs(env) == []
    env.registration.register()
    assert len(draw_funcs(env)) == 1
    env.registration.register()  # idempotent second register
    assert len(draw_funcs(env)) == 1
    env.registration.unregister()
    assert draw_funcs(env) == []
    env.registration.unregister()  # idempotent second unregister
    assert draw_funcs(env) == []


def test_append_purges_stale_callback_from_previous_module_instance(blender_env):
    env = blender_env

    def stale(panel, context):
        pass

    stale._clothnext_add_entry = True
    env.bpy.types.PHYSICS_PT_add.append(stale)
    env.registration.register()
    funcs = draw_funcs(env)
    assert len(funcs) == 1
    assert funcs[0] is env.physics_ui._draw_add_physics_entry
    env.registration.unregister()


# --- 10: repeated register/unregister cycles ---------------------------------------

def test_repeated_register_unregister_cycles_are_clean(blender_env):
    env = blender_env
    for _ in range(3):
        env.registration.register()
        assert len(draw_funcs(env)) == 1
        assert len(env.bpy.registry) == len(set(env.bpy.registry))
        env.registration.unregister()
        assert env.bpy.registry == []
        assert draw_funcs(env) == []
        assert not hasattr(env.bpy.types.Object, "cloth_next")


# --- 11: PointerProperty removed on unregister --------------------------------------

def test_pointer_property_removed_on_unregister(blender_env):
    env = blender_env
    env.registration.register()
    assert hasattr(env.bpy.types.Object, "cloth_next")
    env.registration.unregister()
    assert not hasattr(env.bpy.types.Object, "cloth_next")


# --- rollback on partial failure ----------------------------------------------------

def test_partial_registration_failure_rolls_back_everything(blender_env, monkeypatch):
    env = blender_env

    def explode():
        raise RuntimeError("simulated append failure")

    monkeypatch.setattr(env.physics_ui, "append_add_physics_entry", explode)
    with pytest.raises(RuntimeError, match="simulated append failure"):
        env.registration.register()
    assert env.bpy.registry == []
    assert not hasattr(env.bpy.types.Object, "cloth_next")
    assert draw_funcs(env) == []
    # a later, healthy register still works
    monkeypatch.undo()
    env.registration.register()
    assert len(draw_funcs(env)) == 1
    env.registration.unregister()


# --- preferences behavior preserved --------------------------------------------------

def test_unregister_still_invokes_preferences_shutdown(blender_env, monkeypatch):
    env = blender_env
    import cloth_next.blender.preferences as preferences
    calls = []
    monkeypatch.setattr(preferences, "shutdown", lambda: calls.append(True))
    env.registration.register()
    env.registration.unregister()
    assert calls == [True]


def test_no_solver_or_simulation_side_effects_in_new_modules():
    import ast
    forbidden = {"subprocess", "socket", "cbor2", "urllib", "requests", "threading"}
    for name in ("object_properties.py", "physics_operators.py", "physics_ui.py"):
        tree = ast.parse((BLENDER_PACKAGE / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & forbidden, f"{name} imports {imported & forbidden}"
