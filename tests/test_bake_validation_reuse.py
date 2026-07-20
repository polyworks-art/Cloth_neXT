# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""A Bake validates once — and the worker thread still never touches ``bpy``."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
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
    scene.cloth.cloth_next.cache_directory = "//cn_cache/"
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

    # One full geometry hash for Cloth and Collider (connectivity + coordinates)
    # and one pass over the pin group for the whole Bake start. The old path
    # repeated both several times
    # (panel model, cache state, pin panel, begin_production_bake, run plan).
    #
    # Reading the vertices themselves is *not* counted here: exporting the mesh
    # to the solver legitimately reads every coordinate once.
    assert scene.counters.foreach_get_calls == 10
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


def test_phase4_scene_and_object_hashes_are_deterministic(env, monkeypatch):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=400)
    module = env.solver_test
    monkeypatch.setattr(module, "resolve_solver", lambda _c: _FakeResolved())
    monkeypatch.setattr(module, "_extract_mesh",
                        lambda obj, _d, needs_edges: _fake_mesh(obj))
    monkeypatch.setattr(module, "without_owned_playback", _noop_context)

    first = module.build_run_plan(scene.context)
    second = module.build_run_plan(scene.context)

    assert first.material_meta["fingerprints"]["scene"] == \
        second.material_meta["fingerprints"]["scene"]
    assert first.material_meta["fingerprints"]["object"] == \
        second.material_meta["fingerprints"]["object"]
    assert first.scene.data_hash != second.scene.data_hash, \
        "transport UUIDs may differ without changing the semantic scene hash"


def test_three_separate_cloths_publish_and_attach_authenticated_caches(
        env, monkeypatch, tmp_path):
    scene = mesh_fixtures.build_cloth_scene(env.bpy, vertex_count=9)
    module = env.solver_test

    def add_cloth(name, vertex_count):
        obj = env.bpy.types.Object(name=name, type="MESH")
        obj.data = mesh_fixtures.build_mesh(vertex_count, name=f"{name}Mesh")
        obj.animation_data = None
        obj.constraints = ()
        obj.matrix_world = tuple(
            tuple(1.0 if row == column else 0.0 for column in range(4))
            for row in range(4))
        obj.vertex_groups = mesh_fixtures.VertexGroups()
        obj.cloth_next.enabled = True
        obj.cloth_next.role = "CLOTH"
        obj.cloth_next.bake_start = 1
        obj.cloth_next.bake_end = 24
        scene.context.scene.objects.insert(-1, obj)
        return obj

    cloths = (scene.cloth, add_cloth("Cloth B", 16),
              add_cloth("Cloth C", 25))
    for obj in cloths:
        env.bpy.data.objects[obj.name] = obj

    monkeypatch.setattr(module, "resolve_solver", lambda _context: _FakeResolved())
    monkeypatch.setattr(
        module, "_extract_mesh",
        lambda obj, _depsgraph, needs_edges: _fake_mesh(obj))
    monkeypatch.setattr(module, "without_owned_playback", _noop_context)
    monkeypatch.setattr(module, "_cache_directory", lambda: tmp_path / "cache")
    monkeypatch.setattr(module.bpy.app, "tempdir", str(tmp_path))

    plan = module.build_run_plan(scene.context)
    assert len(plan.deformables) == 3
    scene_hashes = {
        target.material_meta["fingerprints"]["scene"]
        for target in plan.deformables}
    assert len(scene_hashes) == 1
    assert next(iter(scene_hashes))
    repeated = module.build_run_plan(scene.context)
    assert {
        target.material_meta["fingerprints"]["scene"]
        for target in repeated.deformables} == scene_hashes
    assert repeated.scene.data_hash != plan.scene.data_hash, \
        "transport UUIDs may change without changing the shared scene hash"

    class StubSession:
        def __init__(self, **kwargs):
            self.sink = kwargs["frame_sink"]

        def run(self):
            positions = {
                target.uuid: np.asarray(target.initial_local, dtype=np.float32)
                for target in plan.deformables}
            first = plan.deformables[0]
            for frame in range(1, plan.frame_count):
                self.sink(module.SolverFrame(
                    frame, positions[first.uuid], positions))
            return SimpleNamespace(
                timings={}, solver_mode="OWNED_PROCESS",
                package_version="0.1.0", protocol_version="0.11",
                schema_version="1", bytes_transferred=0)

    monkeypatch.setattr(module, "SolverSession", StubSession)
    while not module._queue.empty():
        module._queue.get_nowait()
    module._worker_main(plan)
    messages = []
    while not module._queue.empty():
        messages.append(module._queue.get_nowait())
    assert messages[-1][0] == "finished"

    module._attach_playback(plan, messages[-1][1])

    for obj, target in zip(cloths, plan.deformables):
        assert len(obj.modifiers) == 1
        assert Path(obj.modifiers[0].filepath) == target.pc2_path
        inspection = module.cache_metadata.inspect_cache(
            target.pc2_path,
            settings_fingerprint=plan.settings_fingerprint,
            geometry_fingerprint=plan.geometry_fingerprint)
        assert inspection.usable, inspection.message


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
    scene.cloth.cloth_next.cache_directory = "//cn_cache/"
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
