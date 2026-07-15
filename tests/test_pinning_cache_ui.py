# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pinning stays exact at Bake; the Cache panel stays honest at draw."""

from __future__ import annotations

import pytest

from cloth_next.bake import cache_metadata, pc2
from tests import mesh_fixtures


@pytest.fixture
def env(blender_env, monkeypatch):
    blender_env.registration.register()
    ui = blender_env.physics_ui
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready · Protocol 0.11"))
    yield blender_env
    blender_env.registration.unregister()


def _state(env):
    return env.solver_test.validation_state


def _states(env):
    return _state(env).ValidationState


def _pin_labels(env, scene):
    layout = mesh_fixtures.draw_panel(env.physics_ui.CLOTHNEXT_PT_pinning,
                                      scene.context)
    return layout.labels


def _cache_labels(env, scene):
    layout = mesh_fixtures.draw_panel(env.physics_ui.CLOTHNEXT_PT_cache,
                                      scene.context)
    return layout.labels


# ---------------------------------------------------------------------------
# Pinning: the UI never scans, the Bake always does.

def test_disabled_pinning_scans_no_vertices_in_the_ui(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=50_000)
    scene.counters.reset()
    labels = _pin_labels(env, scene)
    assert "Static hard Pinning is disabled" in labels
    assert scene.counters.full_mesh_scans == 0


def test_enabled_pinning_scans_no_vertices_in_the_ui(env):
    """Enabling pinning marks the object dirty; the panel says so and scans nothing."""
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=50_000,
                                            pinning=True)
    scene.counters.reset()
    labels = _pin_labels(env, scene)
    assert "Pin selection changed · validation required" in labels
    assert scene.counters.vertex_group_scans == 0
    assert scene.counters.full_mesh_scans == 0


def test_never_validated_pinning_says_it_will_be_validated_at_bake(env):
    """A freshly loaded file: UNKNOWN, not a fabricated count."""
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=50_000,
                                            pinning=True)
    _state(env).clear()  # as after a file load
    scene.counters.reset()

    labels = _pin_labels(env, scene)
    assert "Pin selection will be validated before Bake" in labels
    assert scene.counters.full_mesh_scans == 0


def test_validated_pin_count_is_displayed_from_the_record(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True, pinned_fraction=0.25)
    env.solver_test.validate_scene(scene.context)

    scene.counters.reset()
    labels = _pin_labels(env, scene)
    assert "Pinned Vertices: 100" in labels
    assert scene.counters.full_mesh_scans == 0, "the count came from the record"


def test_changed_pin_selection_is_reported_as_needing_validation(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True)
    env.solver_test.validate_scene(scene.context)
    assert "Pinned Vertices: 100" in _pin_labels(env, scene)

    scene.cloth.cloth_next.pin_mode = "FOLLOW_ANIMATION"  # a settings change

    labels = _pin_labels(env, scene)
    assert "Pin selection changed · validation required" in labels
    assert "Pinned Vertices: 100" not in labels


def test_deleted_pin_group_is_reported_without_a_scan(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=50_000,
                                            pinning=True)
    scene.cloth.vertex_groups.remove_group(mesh_fixtures.PIN_GROUP)
    scene.counters.reset()

    labels = _pin_labels(env, scene)
    assert "The selected Pin Group no longer exists." in labels
    assert scene.counters.full_mesh_scans == 0


def test_deleted_pin_group_is_rejected_at_bake(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True)
    scene.cloth.vertex_groups.remove_group(mesh_fixtures.PIN_GROUP)

    with pytest.raises(env.solver_test.SceneValidationError,
                       match="Pin Group no longer exists"):
        env.solver_test.validate_scene(scene.context)


def test_full_validation_returns_the_exact_pin_indices(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True, pinned_fraction=0.25)
    snapshot = env.solver_test.validate_scene(scene.context)
    indices = snapshot.pin_membership.vertex_indices

    assert indices == tuple(range(100))          # the seeded membership
    assert list(indices) == sorted(indices)      # original vertex order


def test_weight_below_the_threshold_stays_unpinned(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True, pinned_fraction=0.25)
    from cloth_next.pinning import STATIC_PIN_WEIGHT_THRESHOLD
    mesh = scene.cloth.data
    mesh.group_weights[0] = STATIC_PIN_WEIGHT_THRESHOLD  # exactly at, not above
    mesh.group_weights[1] = STATIC_PIN_WEIGHT_THRESHOLD / 2

    snapshot = env.solver_test.validate_scene(scene.context)
    indices = snapshot.pin_membership.vertex_indices
    assert 0 not in indices and 1 not in indices
    assert len(indices) == 98


def test_bake_scans_the_pin_group_exactly_once(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=5_000,
                                            pinning=True)
    vertex_count = len(scene.cloth.data.vertices)
    scene.counters.reset()
    env.solver_test.validate_scene(scene.context)
    # One membership lookup per vertex, one pass — not two, not four.
    assert scene.counters.vertex_group_scans == vertex_count
    assert scene.counters.vertex_scans == 1
    # Cloth and Collider connectivity plus coordinates are authenticated once.
    assert scene.counters.foreach_get_calls == 10


# ---------------------------------------------------------------------------
# Cache: honest states, no geometry work in draw.

def _bake_and_record(env, scene):
    """Simulate a completed bake: store both fingerprint halves + the record."""
    snapshot = env.solver_test.validate_scene(scene.context)
    mesh_fixtures.attach_cache(
        scene.cloth,
        settings_fingerprint=snapshot.settings_fingerprint,
        geometry_fingerprint=snapshot.geometry_fingerprint,
        version=env.solver_test.BAKE_FINGERPRINT_VERSION)
    return snapshot


def test_validated_cache_reports_ready(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _bake_and_record(env, scene)

    scene.counters.reset()
    assert env.physics_ui._cache_state(scene.context) == ("MATCHING",
                                                          "Cache ready")
    assert scene.counters.full_mesh_scans == 0


def test_material_change_reports_stale_without_a_geometry_scan(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=100_000)
    _bake_and_record(env, scene)

    scene.cloth.cloth_next.material.bend_resistance = 77.0
    scene.counters.reset()

    state, label = env.physics_ui._cache_state(scene.context)
    assert (state, label) == ("STALE", "Cache stale · settings changed")
    assert scene.counters.full_mesh_scans == 0
    assert scene.counters.foreach_get_calls == 0


def test_mesh_change_reports_needs_validation_not_ready(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=100_000)
    _bake_and_record(env, scene)
    assert env.physics_ui._cache_state(scene.context)[0] == "MATCHING"

    # A depsgraph geometry update — the UI must stop claiming the cache matches.
    _state(env).mark_geometry_dirty(scene.cloth)
    scene.counters.reset()

    state, label = env.physics_ui._cache_state(scene.context)
    assert state == "NEEDS_VALIDATION"
    assert label == "Cache needs validation · mesh may have changed"
    assert scene.counters.full_mesh_scans == 0


def test_full_validation_detects_a_real_topology_mismatch(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _bake_and_record(env, scene)

    scene.cloth.data.drop_edge(0)          # the mesh really did change
    _state(env).mark_geometry_dirty(scene.cloth)
    env.solver_test.validate_scene(scene.context)   # the expensive, exact check

    state, label = env.physics_ui._cache_state(scene.context)
    assert (state, label) == ("STALE", "Cache stale · mesh changed")


def test_position_only_change_invalidates_the_solver_input(env):
    """Moving a vertex changes the solver input even with fixed topology."""
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _bake_and_record(env, scene)

    scene.cloth.data.move_vertex(3, 5.0)
    _state(env).mark_geometry_dirty(scene.cloth)
    env.solver_test.validate_scene(scene.context)

    assert env.physics_ui._cache_state(scene.context) == (
        "STALE", "Cache stale · mesh changed")


def test_static_collider_vertex_change_invalidates_cache(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _bake_and_record(env, scene)

    scene.collider.data.move_vertex(2, 3.0)
    _state(env).mark_geometry_dirty(scene.cloth)
    env.solver_test.validate_scene(scene.context)

    assert env.physics_ui._cache_state(scene.context) == (
        "STALE", "Cache stale · mesh changed")


def test_collider_transform_change_invalidates_without_mesh_scan(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=100_000)
    _bake_and_record(env, scene)
    scene.counters.reset()

    matrix = [list(row) for row in scene.collider.matrix_world]
    matrix[0][3] = 2.0
    scene.collider.matrix_world = tuple(tuple(row) for row in matrix)

    assert env.physics_ui._cache_state(scene.context) == (
        "STALE", "Cache stale · settings changed")
    assert scene.counters.foreach_get_calls == 0
    assert scene.counters.full_mesh_scans == 0


def test_unvalidated_session_never_claims_the_cache_is_ready(env):
    """After a file load the record is UNKNOWN — the panel must not lie."""
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    _bake_and_record(env, scene)
    _state(env).clear()  # a fresh session: nothing has been validated yet

    state, label = env.physics_ui._cache_state(scene.context)
    assert state == "NEEDS_VALIDATION"
    assert "Cache ready" not in label


def test_legacy_cache_without_a_geometry_half_needs_validation(env):
    """A result baked by an older version is never presented as matching."""
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    snapshot = env.solver_test.validate_scene(scene.context)
    mesh_fixtures.attach_cache(
        scene.cloth, settings_fingerprint=snapshot.settings_fingerprint,
        geometry_fingerprint="", version=0)  # the old sidecar shape

    state, _label = env.physics_ui._cache_state(scene.context)
    assert state == "NEEDS_VALIDATION"


def test_failed_validation_preserves_the_existing_cache(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400,
                                            pinning=True)
    _bake_and_record(env, scene)
    settings = scene.cloth.cloth_next
    baked = (settings.baked_settings_fingerprint,
             settings.baked_geometry_fingerprint,
             settings.baked_fingerprint_version,
             len(scene.cloth.modifiers))

    scene.cloth.vertex_groups.remove_group(mesh_fixtures.PIN_GROUP)
    with pytest.raises(env.solver_test.SceneValidationError):
        env.solver_test.validate_scene(scene.context)

    assert (settings.baked_settings_fingerprint,
            settings.baked_geometry_fingerprint,
            settings.baked_fingerprint_version,
            len(scene.cloth.modifiers)) == baked, "the old cache was destroyed"
    state, label = env.physics_ui._cache_state(scene.context)
    assert state == "INVALID" and "Pin Group no longer exists" in label


def test_explicit_validation_detects_on_disk_cache_corruption(env, tmp_path):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    snapshot = env.solver_test.validate_scene(scene.context)
    path = tmp_path / "cn_test_cloth_integrity.pc2"
    pc2.write_pc2(path, [((0.0, 0.0, 0.0),)])
    partial = cache_metadata.partial_metadata(
        cache_path=path,
        fingerprints={"settings": snapshot.settings_fingerprint,
                      "geometry": snapshot.geometry_fingerprint,
                      "combined": snapshot.combined_fingerprint,
                      "topology": snapshot.topology_signature,
                      "object": "object", "scene": "scene"},
        identities={},
        expected={"vertex_count": 1, "frame_count": 1,
                  "start_frame": 0.0, "sample_rate": 1.0},
        details={})
    complete = cache_metadata.completed_metadata(partial, cache_path=path)
    cache_metadata.write_atomic(cache_metadata.sidecar_path(path), complete)
    modifier = mesh_fixtures.attach_cache(
        scene.cloth, settings_fingerprint=snapshot.settings_fingerprint,
        geometry_fingerprint=snapshot.geometry_fingerprint,
        version=env.solver_test.BAKE_FINGERPRINT_VERSION)
    modifier.filepath = str(path)
    scene.cloth.cloth_next_cache_path = str(path)
    scene.cloth.cloth_next.baked_cache_condition = "READY"

    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    env.solver_test.validate_scene(scene.context)

    state, label = env.physics_ui._cache_state(scene.context)
    assert state == "INVALID"
    assert "hash mismatch" in label


def test_no_cache_state_call_reads_the_mesh_at_any_size(env):
    for vertex_count in (10_000, 500_000):
        scene = mesh_fixtures.build_cloth_scene(env.bpy,
                                                vertex_count=vertex_count)
        _bake_and_record(env, scene)
        scene.counters.reset()
        for _ in range(100):
            env.physics_ui._cache_state(scene.context)
        assert scene.counters.full_mesh_scans == 0
        assert scene.counters.foreach_get_calls == 0
