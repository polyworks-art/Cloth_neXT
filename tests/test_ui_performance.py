# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender UI paths must stay cheap.

Two independent guards:

* a **structural** guard that parses ``physics_ui.py`` and follows the call
  graph out of every ``draw()``/``draw_header()``/``poll()`` to prove none of
  them can reach a known-expensive function; and
* a **behavioural** guard that runs the real panels against instrumented
  synthetic meshes and asserts 100 redraws perform zero mesh scans.

The structural test is what stops the regression coming back: a future edit
that calls ``_snapshot_static_pin`` from a panel fails here even if it happens
to be fast on the author's small test mesh.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests import mesh_fixtures

UI_SOURCE = Path("cloth_next/blender/physics_ui.py")

# Everything that reads mesh data, hashes topology, scans a vertex group,
# resolves a path on disk, or starts the solver.
FORBIDDEN_IN_DRAW = {
    "_snapshot_static_pin",
    "current_settings_fingerprint",
    "validate_scene",
    "mesh_topology_signature",
    "reference_topology_signature",
    "_scan_pin_indices",
    "_extract_mesh",
    "build_run_plan",
    "begin_production_bake",
    "build_parameter_inspection",
    "to_mesh",
    "calc_loop_triangles",
    "foreach_get",
    "is_cloth_next_playback_modifier",  # resolves paths on disk
    "start_run",
    "resolve_solver",
}

ENTRY_POINTS = {"draw", "draw_header", "poll"}


def _module_functions(tree):
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _panel_entry_points(tree):
    """Every draw/draw_header/poll defined on a class in the module."""
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if (isinstance(item, ast.FunctionDef)
                    and item.name in ENTRY_POINTS):
                yield f"{node.name}.{item.name}", item


def _called_names(node):
    """Every callable name referenced in a subtree, bare or dotted."""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _reachable(start, functions):
    """Names reachable from ``start``, following module-level helpers."""
    seen, pending, reached = set(), [start], set()
    while pending:
        node = pending.pop()
        for name in _called_names(node):
            reached.add(name)
            helper = functions.get(name)
            if helper is not None and name not in seen:
                seen.add(name)
                pending.append(helper)
    return reached


@pytest.fixture(scope="module")
def ui_tree():
    return ast.parse(UI_SOURCE.read_text(encoding="utf-8"))


def test_no_panel_entry_point_reaches_an_expensive_function(ui_tree):
    functions = _module_functions(ui_tree)
    offenders = {}
    for label, node in _panel_entry_points(ui_tree):
        forbidden = _reachable(node, functions) & FORBIDDEN_IN_DRAW
        if forbidden:
            offenders[label] = sorted(forbidden)
    assert not offenders, (
        "Blender UI entry points must never reach mesh work; found: "
        f"{offenders}")


def test_module_level_draw_helpers_are_also_clean(ui_tree):
    """The appended PHYSICS_PT_add callback is a draw path too."""
    functions = _module_functions(ui_tree)
    entry = functions["_draw_add_physics_entry"]
    assert not (_reachable(entry, functions) & FORBIDDEN_IN_DRAW)


def test_cache_panel_does_not_use_a_geometry_fingerprint(ui_tree):
    functions = _module_functions(ui_tree)
    reached = _reachable(functions["_cache_state"], functions)
    assert "geometry_fingerprint" not in reached
    assert "cheap_settings_fingerprint" in reached


def test_pinning_panel_uses_the_cheap_summary(ui_tree):
    for label, node in _panel_entry_points(ui_tree):
        if label == "CLOTHNEXT_PT_pinning.draw":
            assert "cheap_pin_summary" in _called_names(node)
            return
    pytest.fail("CLOTHNEXT_PT_pinning.draw not found")


# ---------------------------------------------------------------------------
# Behavioural: the real panels, real draw(), instrumented meshes.

REDRAWS = 100


def _panels(ui):
    return (ui.CLOTHNEXT_PT_physics, ui.CLOTHNEXT_PT_overview,
            ui.CLOTHNEXT_PT_solver, ui.CLOTHNEXT_PT_material,
            ui.CLOTHNEXT_PT_pinning, ui.CLOTHNEXT_PT_damping,
            ui.CLOTHNEXT_PT_collisions, ui.CLOTHNEXT_PT_cache,
            ui.CLOTHNEXT_PT_advanced)


def _ready_solver(ui, monkeypatch):
    monkeypatch.setattr(ui, "_solver_status",
                        lambda _c: ui._SolverStatus(True, "Ready · Protocol 0.11"))


@pytest.mark.parametrize("pinning", [False, True])
@pytest.mark.parametrize("vertex_count", [10_000, 100_000])
def test_100_redraws_perform_zero_mesh_work(blender_env, monkeypatch,
                                            vertex_count, pinning):
    env = blender_env
    env.registration.register()
    _ready_solver(env.physics_ui, monkeypatch)
    scene = mesh_fixtures.build_cloth_scene(
        env.bpy, vertex_count=vertex_count, pinning=pinning)
    panels = _panels(env.physics_ui)

    scene.counters.reset()
    for _ in range(REDRAWS):
        for panel_cls in panels:
            if not panel_cls.poll(scene.context):
                continue
            mesh_fixtures.draw_panel(panel_cls, scene.context)

    counters = scene.counters
    assert counters.vertex_scans == 0
    assert counters.edge_scans == 0
    assert counters.polygon_scans == 0
    assert counters.loop_scans == 0
    assert counters.vertex_group_scans == 0
    assert counters.foreach_get_calls == 0
    assert counters.to_mesh_calls == 0
    assert counters.full_mesh_scans == 0
    env.registration.unregister()


def test_poll_never_touches_mesh_data(blender_env):
    env = blender_env
    env.registration.register()
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=10_000,
                                            pinning=True)
    scene.counters.reset()
    for _ in range(REDRAWS):
        for panel_cls in _panels(env.physics_ui):
            panel_cls.poll(scene.context)
    assert scene.counters.full_mesh_scans == 0
    env.registration.unregister()


def test_draw_cost_does_not_grow_with_mesh_size(blender_env, monkeypatch):
    """The same redraw does the same (zero) mesh work at 10k and at 500k."""
    env = blender_env
    env.registration.register()
    _ready_solver(env.physics_ui, monkeypatch)
    work = {}
    for vertex_count in (10_000, 500_000):
        scene = mesh_fixtures.build_cloth_scene(
            env.bpy, vertex_count=vertex_count, pinning=True)
        scene.counters.reset()
        for _ in range(10):
            for panel_cls in _panels(env.physics_ui):
                if panel_cls.poll(scene.context):
                    mesh_fixtures.draw_panel(panel_cls, scene.context)
        work[vertex_count] = scene.counters.full_mesh_scans
    assert work == {10_000: 0, 500_000: 0}
    env.registration.unregister()


def test_selection_change_alone_starts_no_mesh_scan(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    _ready_solver(env.physics_ui, monkeypatch)
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=10_000,
                                            pinning=True)
    scene.counters.reset()
    for target in (scene.cloth, scene.collider) * 25:
        scene.context.object = target
        scene.context.active_object = target
        for panel_cls in _panels(env.physics_ui):
            if panel_cls.poll(scene.context):
                mesh_fixtures.draw_panel(panel_cls, scene.context)
    assert scene.counters.full_mesh_scans == 0
    env.registration.unregister()


def test_redraw_never_mutates_the_cache(blender_env, monkeypatch):
    env = blender_env
    env.registration.register()
    _ready_solver(env.physics_ui, monkeypatch)
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=1_000)
    modifier = mesh_fixtures.attach_cache(
        scene.cloth, settings_fingerprint="baked-settings",
        geometry_fingerprint="baked-geometry",
        version=env.solver_test.BAKE_FINGERPRINT_VERSION)

    before = (len(scene.cloth.modifiers), modifier.filepath,
              scene.cloth.cloth_next.baked_settings_fingerprint,
              scene.cloth.cloth_next.baked_geometry_fingerprint)
    for _ in range(REDRAWS):
        for panel_cls in _panels(env.physics_ui):
            if panel_cls.poll(scene.context):
                mesh_fixtures.draw_panel(panel_cls, scene.context)
    after = (len(scene.cloth.modifiers), modifier.filepath,
             scene.cloth.cloth_next.baked_settings_fingerprint,
             scene.cloth.cloth_next.baked_geometry_fingerprint)
    assert before == after
    assert scene.counters.full_mesh_scans == 0
    env.registration.unregister()
