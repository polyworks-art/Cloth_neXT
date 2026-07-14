# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dirty-state rules, handler lifecycle, and the cheap/expensive split.

The contract under test: changing a property or touching the mesh must be
*cheap* and must leave the object recorded as needing re-validation. Only a
full validation may clear that, and it must be exact.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests import mesh_fixtures


@pytest.fixture
def env(blender_env):
    blender_env.registration.register()
    yield blender_env
    blender_env.registration.unregister()


def _state(env):
    return env.solver_test.validation_state


def _states(env):
    return _state(env).ValidationState


def _validated(env, scene):
    """Run one real full validation so the object is recorded VALID."""
    return env.solver_test.validate_scene(scene.context)


# ---------------------------------------------------------------------------
# Dirty rules

def test_material_change_marks_settings_dirty(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    assert _state(env).record_for(scene.cloth).state is _states(env).VALID

    scene.counters.reset()
    scene.cloth.cloth_next.material.stretch_resistance = 4321.0

    record = _state(env).record_for(scene.cloth)
    assert record.state is _states(env).DIRTY
    assert record.settings_dirty
    assert scene.counters.full_mesh_scans == 0, "marking dirty must not scan"


@pytest.mark.parametrize("attribute,value", [
    ("pinning_enabled", True),
    ("pin_group", "Pins"),
    ("pin_mode", "FOLLOW_ANIMATION"),
    ("bake_start", 5),
    ("bake_end", 90),
    ("role", "COLLIDER"),
    ("enabled", False),
    ("cache_directory", "//somewhere/"),
])
def test_solver_visible_property_changes_mark_dirty(env, attribute, value):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    scene.counters.reset()

    setattr(scene.cloth.cloth_next, attribute, value)

    record = _state(env).record_for(scene.cloth)
    assert record.state is _states(env).DIRTY, f"{attribute} must mark dirty"
    assert scene.counters.full_mesh_scans == 0


@pytest.mark.parametrize("group,attribute,value", [
    ("collision", "enabled", False),
    ("collision", "surface_grip", 0.9),
    ("pressure", "enable_inflate", True),
    ("pressure", "inflate_pressure", 3.0),
    ("damping", "shape_damping", 0.05),
])
def test_nested_group_changes_mark_dirty(env, group, attribute, value):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    scene.counters.reset()

    setattr(getattr(scene.cloth.cloth_next, group), attribute, value)

    assert _state(env).record_for(scene.cloth).state is _states(env).DIRTY
    assert scene.counters.full_mesh_scans == 0


def test_every_solver_quality_property_marks_dirty(env):
    """Quality is scene-wide, so its update callback demotes every record."""
    quality_cls = env.object_properties.CLOTHNEXT_PG_solver_quality_settings
    from tests.fake_bpy import _resolved_props
    props = _resolved_props(quality_cls)
    assert set(props) == {"time_step", "min_newton_steps", "cg_max_iter",
                          "cg_tol"}
    for name, prop in props.items():
        assert prop.keywords.get("update") is not None, \
            f"solver quality '{name}' must mark the scene dirty"

    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    scene.counters.reset()

    _state(env).mark_all_settings_dirty()

    record = _state(env).record_for(scene.cloth)
    assert record.state is _states(env).DIRTY and record.settings_dirty
    assert scene.counters.full_mesh_scans == 0


def test_dirty_state_survives_until_a_full_validation(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    scene.cloth.cloth_next.material.bend_resistance = 42.0
    states = _states(env)

    for _ in range(20):  # redraws, selection changes, timeline steps…
        assert _state(env).record_for(scene.cloth).state is states.DIRTY

    _validated(env, scene)
    assert _state(env).record_for(scene.cloth).state is states.VALID


def test_depsgraph_update_marks_geometry_dirty_without_reading_the_mesh(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=100_000)
    _validated(env, scene)
    scene.counters.reset()

    depsgraph = SimpleNamespace(updates=[SimpleNamespace(
        id=scene.cloth, is_updated_geometry=True, is_updated_transform=False)])
    handler = env.bpy.app.handlers.depsgraph_update_post[0]
    handler(scene.context.scene, depsgraph)

    record = _state(env).record_for(scene.cloth)
    assert record.state is _states(env).DIRTY
    assert record.geometry_dirty
    assert scene.counters.full_mesh_scans == 0
    assert scene.counters.foreach_get_calls == 0


def test_depsgraph_handler_ignores_objects_without_cloth_next(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    other = env.bpy.types.Object(name="Bystander", type="MESH")
    other.cloth_next.enabled = False

    depsgraph = SimpleNamespace(updates=[SimpleNamespace(
        id=other, is_updated_geometry=True, is_updated_transform=False)])
    env.bpy.app.handlers.depsgraph_update_post[0](scene.context.scene, depsgraph)

    assert _state(env).record_for(scene.cloth).state is _states(env).VALID


def test_repeated_depsgraph_updates_are_cheap_and_idempotent(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=100_000)
    _validated(env, scene)
    scene.counters.reset()

    handler = env.bpy.app.handlers.depsgraph_update_post[0]
    depsgraph = SimpleNamespace(updates=[SimpleNamespace(
        id=scene.cloth, is_updated_geometry=True, is_updated_transform=False)])
    for _ in range(500):  # a viewport drag
        handler(scene.context.scene, depsgraph)

    assert _state(env).record_for(scene.cloth).state is _states(env).DIRTY
    assert scene.counters.full_mesh_scans == 0


def test_swapped_mesh_datablock_invalidates_the_record(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    assert _state(env).record_for(scene.cloth).state is _states(env).VALID

    scene.cloth.data = mesh_fixtures.build_mesh(400, name="OtherMesh",
                                                counters=scene.counters)
    assert _state(env).record_for(scene.cloth).state is _states(env).DIRTY


# ---------------------------------------------------------------------------
# Validation outcomes

def test_failed_validation_records_invalid_with_a_message(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True)
    scene.cloth.vertex_groups.remove_group(mesh_fixtures.PIN_GROUP)

    with pytest.raises(env.solver_test.SceneValidationError):
        env.solver_test.validate_scene(scene.context)

    record = _state(env).record_for(scene.cloth)
    assert record.state is _states(env).INVALID
    assert "Pin Group no longer exists" in record.message


def test_successful_validation_records_the_pin_count(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True, pinned_fraction=0.25)
    snapshot = env.solver_test.validate_scene(scene.context)

    record = _state(env).record_for(scene.cloth)
    assert record.state is _states(env).VALID
    assert record.pin_count == len(snapshot.pin_membership.vertex_indices)
    assert record.pin_count == 100  # 25% of 400
    assert record.pin_group == mesh_fixtures.PIN_GROUP


def test_validation_is_the_only_thing_that_scans(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=10_000,
                                            pinning=True)
    scene.counters.reset()
    env.solver_test.validate_scene(scene.context)

    assert scene.counters.vertex_group_scans == 10_000  # exactly one pass
    assert scene.counters.foreach_get_calls == 4        # one topology hash


# ---------------------------------------------------------------------------
# Lifecycle

def test_handlers_registered_exactly_once(env):
    handlers = env.bpy.app.handlers
    assert len(handlers.depsgraph_update_post) == 1
    assert len(handlers.load_post) == 1
    assert len(handlers.undo_post) == 1
    assert len(handlers.redo_post) == 1


def test_unregister_removes_every_handler(blender_env):
    env = blender_env
    env.registration.register()
    assert env.solver_test.validation_state.handler_count() == 4
    env.registration.unregister()
    assert env.solver_test.validation_state.handler_count() == 0
    handlers = env.bpy.app.handlers
    assert handlers.depsgraph_update_post == []
    assert handlers.load_post == []


def test_reload_cycle_creates_no_duplicate_handlers(blender_env):
    env = blender_env
    for _ in range(3):
        env.registration.register()
        env.registration.unregister()
    env.registration.register()
    assert env.solver_test.validation_state.handler_count() == 4
    env.registration.unregister()


def test_stale_handler_from_a_skipped_unregister_is_purged(blender_env):
    """A reload that never called unregister() leaves an orphaned callback."""
    env = blender_env
    state = __import__("cloth_next.blender.validation_state",
                       fromlist=["validation_state"])

    def orphan(scene, depsgraph=None):
        raise AssertionError("the stale handler must never run")

    orphan._clothnext_validation_handler = True
    env.bpy.app.handlers.depsgraph_update_post.append(orphan)

    env.registration.register()
    assert orphan not in env.bpy.app.handlers.depsgraph_update_post
    assert state.handler_count() == 4
    env.registration.unregister()


def test_file_load_clears_the_runtime_state(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    assert _state(env).record_for(scene.cloth).state is _states(env).VALID

    env.bpy.app.handlers.load_post[0](None)

    # A fresh file starts at UNKNOWN — never at a stale "VALID".
    assert _state(env).record_for(scene.cloth).state is _states(env).UNKNOWN


def test_object_deletion_drops_the_runtime_state(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    _state(env).prune([])  # the object no longer exists
    assert _state(env).record_for(scene.cloth).state is _states(env).UNKNOWN


def test_undo_redo_prunes_without_crashing(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    for handler in (env.bpy.app.handlers.undo_post[0],
                    env.bpy.app.handlers.redo_post[0]):
        handler(scene.context.scene)  # bpy.data.objects is empty -> prunes
    assert _state(env).record_for(scene.cloth).state is _states(env).UNKNOWN


def test_removing_cloth_next_forgets_the_record(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    assert scene.cloth.cloth_next.id_data is scene.cloth
    env.object_properties.reset_settings(scene.cloth.cloth_next)
    assert _state(env).record_for(scene.cloth).state is _states(env).UNKNOWN


def test_no_strong_reference_to_blender_objects_is_retained(env):
    """Records must be keyed by name, so they cannot keep an Object alive."""
    import gc
    import weakref

    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _validated(env, scene)
    reference = weakref.ref(scene.cloth)

    scene.context.object = None
    scene.context.active_object = None
    scene.context.scene.objects = []
    scene.cloth = None
    gc.collect()

    assert reference() is None, "validation_state pinned a Blender object"
    # …and the record is still there, keyed by name, ready to be pruned.
    assert "Cloth" in _state(env)._records
