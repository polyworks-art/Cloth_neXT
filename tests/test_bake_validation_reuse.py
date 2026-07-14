# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A Bake validates once — and the worker thread still never touches ``bpy``."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cloth_next.bake.controller import InvalidTransition, shared_controller
from cloth_next.bake.status import BakeState
from tests import mesh_fixtures

SOLVER_TEST_SOURCE = Path("cloth_next/blender/solver_test.py")


def _reset_controller():
    """Drive the controller to a terminal state before resetting it.

    begin_production_bake() leaves it in PREPARING, and PREPARING -> IDLE is
    not a legal transition; it has to fail first.
    """
    if shared_controller.snapshot().state is not BakeState.IDLE:
        try:
            shared_controller.fail("test cleanup")
        except InvalidTransition:
            pass
        shared_controller.reset()


@pytest.fixture
def env(blender_env):
    _reset_controller()
    blender_env.registration.register()
    yield blender_env
    blender_env.registration.unregister()
    _reset_controller()


# ---------------------------------------------------------------------------
# Phase 10: exactly one authoritative validation per Bake start.

def test_bake_start_hashes_topology_and_scans_pins_exactly_once(env,
                                                                monkeypatch):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=10_000,
                                            pinning=True)
    module = env.solver_test
    vertex_count = len(scene.cloth.data.vertices)

    # Stop after the plan is built: the solver itself is out of scope here.
    monkeypatch.setattr(module, "_continue_production_bake",
                        lambda _c, job_id, plan: (job_id, False))
    monkeypatch.setattr(module, "resolve_solver",
                        lambda _c: _FakeResolved())
    monkeypatch.setattr(module, "_extract_mesh",
                        lambda obj, _d, needs_edges: _fake_mesh(obj))
    monkeypatch.setattr(module, "without_owned_playback", _noop_context)

    scene.counters.reset()
    module.begin_production_bake(scene.context)

    # One topology hash (four foreach_get buffers) and one pass over the pin
    # group for the whole Bake start. The old path repeated both several times
    # (panel model, cache state, pin panel, begin_production_bake, run plan).
    #
    # Reading the vertices themselves is *not* counted here: exporting the mesh
    # to the solver legitimately reads every coordinate once.
    assert scene.counters.foreach_get_calls == 4
    assert scene.counters.vertex_group_scans == vertex_count


def test_run_plan_reuses_the_supplied_snapshot(env, monkeypatch):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=2_500,
                                            pinning=True)
    module = env.solver_test
    monkeypatch.setattr(module, "resolve_solver", lambda _c: _FakeResolved())
    monkeypatch.setattr(module, "_extract_mesh",
                        lambda obj, _d, needs_edges: _fake_mesh(obj))
    monkeypatch.setattr(module, "without_owned_playback", _noop_context)

    snapshot = module.validate_scene(scene.context)
    scene.counters.reset()

    plan = module.build_run_plan(scene.context, snapshot=snapshot)

    assert scene.counters.foreach_get_calls == 0, "topology hashed twice"
    assert scene.counters.vertex_group_scans == 0, "pin group scanned twice"
    assert plan.settings_fingerprint == snapshot.settings_fingerprint
    assert plan.geometry_fingerprint == snapshot.geometry_fingerprint


def test_unregister_completes_even_mid_bake(blender_env, monkeypatch):
    """Disabling the add-on after validation but before the worker starts.

    The controller sits in PREPARING, which has no legal direct transition to
    IDLE. unregister() must still tear everything down completely.
    """
    _reset_controller()
    env = blender_env
    env.registration.register()
    module = env.solver_test
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    monkeypatch.setattr(module, "_continue_production_bake",
                        lambda _c, job_id, plan: (job_id, False))
    monkeypatch.setattr(module, "resolve_solver", lambda _c: _FakeResolved())
    monkeypatch.setattr(module, "_extract_mesh",
                        lambda obj, _d, needs_edges: _fake_mesh(obj))
    monkeypatch.setattr(module, "without_owned_playback", _noop_context)
    module.begin_production_bake(scene.context)
    assert shared_controller.snapshot().state is BakeState.PREPARING

    env.registration.unregister()  # must not raise InvalidTransition

    assert shared_controller.snapshot().state is BakeState.IDLE
    assert module.validation_state.handler_count() == 0
    assert env.bpy.app.handlers.depsgraph_update_post == []
    _reset_controller()


def test_bake_fingerprint_combines_both_halves(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    module = env.solver_test
    snapshot = module.validate_scene(scene.context)

    assert snapshot.combined_fingerprint == module.bake_fingerprint(
        snapshot.settings_fingerprint, snapshot.geometry_fingerprint)
    assert snapshot.settings_fingerprint != snapshot.geometry_fingerprint
    assert snapshot.combined_fingerprint not in (
        snapshot.settings_fingerprint, snapshot.geometry_fingerprint)


def test_settings_fingerprint_ignores_the_mesh_but_tracks_settings(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=2_500)
    module = env.solver_test

    scene.counters.reset()
    before = module.cheap_settings_fingerprint(scene.context)
    assert scene.counters.full_mesh_scans == 0
    assert scene.counters.foreach_get_calls == 0

    scene.cloth.data.drop_edge(0)  # topology changed…
    assert module.cheap_settings_fingerprint(scene.context) == before, \
        "the settings fingerprint must not depend on the mesh"

    scene.cloth.cloth_next.material.bend_resistance = 99.0  # …settings changed
    assert module.cheap_settings_fingerprint(scene.context) != before


def test_geometry_fingerprint_tracks_topology_and_pins(env):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=2_500,
                                            pinning=True)
    module = env.solver_test
    before = module.validate_scene(scene.context).geometry_fingerprint

    scene.cloth.data.drop_edge(0)
    after = module.validate_scene(scene.context).geometry_fingerprint
    assert after != before


# ---------------------------------------------------------------------------
# Threading contract: the worker never reaches bpy.

def _module_functions(tree):
    return {node.name: node for node in tree.body
            if isinstance(node, ast.FunctionDef)}


def _names_used(node):
    used = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            used.add(child.id)
        elif isinstance(child, ast.Attribute):
            value = child.value
            if isinstance(value, ast.Name):
                used.add(value.id)
    return used


def _called(node):
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            names.add(child.func.id)
    return names


def test_worker_thread_never_touches_bpy():
    """_worker_main and everything it calls in-module must be bpy-free."""
    tree = ast.parse(SOLVER_TEST_SOURCE.read_text(encoding="utf-8"))
    functions = _module_functions(tree)

    seen, pending, offenders = set(), ["_worker_main"], []
    while pending:
        name = pending.pop()
        node = functions.get(name)
        if node is None or name in seen:
            continue
        seen.add(name)
        if "bpy" in _names_used(node):
            offenders.append(name)
        pending.extend(_called(node) - seen)

    assert "_worker_main" in seen
    assert not offenders, f"worker-thread code touches bpy: {offenders}"


@pytest.mark.parametrize("module_path", [
    "cloth_next/topology.py",
    "cloth_next/pinning.py",
    "cloth_next/materials/formatting.py",
])
def test_pure_modules_never_import_bpy(module_path):
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "bpy" for alias in node.names), module_path
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "bpy", module_path


def test_validation_state_stores_no_blender_data():
    """The runtime cache must hold strings, never Object or Mesh references."""
    source = Path("cloth_next/blender/validation_state.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    record = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.ClassDef)
                  and node.name == "ValidationRecord")
    annotations = {item.target.id: ast.unparse(item.annotation)
                   for item in record.body
                   if isinstance(item, ast.AnnAssign)}
    assert set(annotations.values()) <= {"ValidationState", "int", "str",
                                         "float", "bool"}, annotations


# ---------------------------------------------------------------------------
# Test doubles

class _FakeResolved:
    executable_path = Path("/fake/solver")
    package_version = "1.0"
    protocol_version = "0.11"
    schema_version = "1"

    class mode:
        name = "OWNED_PROCESS"


def _fake_mesh(obj):
    """Evaluated vertices/triangles without a real depsgraph."""
    mesh = obj.data
    vertices = tuple((float(v.co.x), float(v.co.y), float(v.co.z))
                     for v in mesh.vertices)
    triangles = tuple(tri.vertices for tri in mesh.loop_triangles)
    return vertices, triangles


class _noop_context:
    def __init__(self, _obj, _update=None):
        pass

    def __enter__(self):
        return ()

    def __exit__(self, *_exc):
        return False
