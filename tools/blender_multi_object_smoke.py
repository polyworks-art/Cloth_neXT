# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender smoke for multi-deformable validation and export."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
addon = importlib.import_module("cloth_next")
addon.register()
try:
    objects = []
    for name, location in (("MultiClothA", (-0.5, 0.0, 1.0)),
                           ("MultiClothB", (0.5, 0.0, 1.2)),
                           ("MultiCollider", (0.0, 0.0, 0.0))):
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=3, y_subdivisions=3,
                                        location=location)
        obj = bpy.context.object
        obj.name = name
        bpy.ops.clothnext.add_physics()
        objects.append(obj)
    cloth_a, cloth_b, collider = objects
    bpy.context.view_layer.objects.active = collider
    bpy.ops.clothnext.set_object_type(role="COLLIDER")
    bpy.ops.object.empty_add(type="ARROWS", location=(0.0, 0.0, 1.5))
    wind = bpy.context.object
    wind.name = "MultiWind"
    from cloth_next.blender import physics_ui
    assert physics_ui.CLOTHNEXT_PT_empty_force.poll(bpy.context)
    bpy.ops.clothnext.add_physics()
    assert wind.cloth_next.role == "FORCE"
    wind.cloth_next.force.force_type = "WIND"
    wind.cloth_next.force.strength = 1.0
    wind.keyframe_insert(data_path="cloth_next.force.strength", frame=1)
    wind.cloth_next.force.strength = 3.0
    wind.keyframe_insert(data_path="cloth_next.force.strength", frame=3)
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=(0.0, 0.0, 0.0))
    air = bpy.context.object
    air.name = "MultiAirDensity"
    bpy.ops.clothnext.add_physics()
    air.cloth_next.force.force_type = "AIR_DENSITY"
    air.cloth_next.force.air_density = 0.1
    air.keyframe_insert(data_path="cloth_next.force.air_density", frame=1)
    air.cloth_next.force.air_density = 0.5
    air.keyframe_insert(data_path="cloth_next.force.air_density", frame=3)
    for cloth in (cloth_a, cloth_b):
        cloth.cloth_next.bake_start = 1
        cloth.cloth_next.bake_end = 3
        group = cloth.vertex_groups.new(name="Pins")
        group.add([0], 1.0, "REPLACE")
        cloth.cloth_next.pinning_enabled = True
        cloth.cloth_next.pin_group = group.name
        cloth.cloth_next.pin_mode = "FOLLOW_ANIMATION"
        cloth.keyframe_insert("location", frame=1)
        cloth.location.z += 0.15
        cloth.keyframe_insert("location", frame=3)

    from cloth_next.blender import solver_test
    bpy.context.scene.frame_set(1)
    snapshot = solver_test.validate_scene(bpy.context)
    assert len(snapshot.deformables) == 2
    assert snapshot.wind_blender == (0.0, 0.0, 1.0)
    assert {entry.obj.name for entry in snapshot.deformables} == {
        "MultiClothA", "MultiClothB"}
    samples = {entry.obj.name: solver_test._capture_animated_pin(
        bpy.context, entry.obj, snapshot.bake_range,
        entry.pin_membership).samples for entry in snapshot.deformables}

    resolved = SimpleNamespace(
        mode=SimpleNamespace(name="SMOKE"), package_version="test",
        protocol_version="0.11", schema_version="1",
        source_metadata={}, executable_path=Path("smoke-solver"))
    original_resolve = solver_test.resolve_solver
    solver_test.resolve_solver = lambda _context: resolved
    try:
        plan = solver_test.build_run_plan(
            bpy.context, snapshot=snapshot, animated_pin_samples=samples)
    finally:
        solver_test.resolve_solver = original_resolve
    assert len(plan.deformables) == 2
    assert len(plan.scene.dynamic_objects) == 2
    assert len({target.uuid for target in plan.deformables}) == 2
    assert len({target.pc2_path for target in plan.deformables}) == 2
    from cloth_next.ppf.schema import envelope
    params = envelope.loads_envelope(plan.scene.param_payload,
                                     envelope.KIND_PARAM)
    diagnostics = {
        "pin_config_keys": sorted(params.get("pin_config", {})),
        "expected_pin_keys": sorted(target.uuid for target in plan.deformables),
        "scene": params.get("scene"),
        "dynamic_keys": sorted(params.get("dyn_param", {})),
        "dyn_param": params.get("dyn_param"),
    }
    (ROOT / "multi_object_smoke_diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")
    assert set(params["pin_config"]) == {
        target.uuid for target in plan.deformables}
    assert all(len(config) == 1 for config in params["pin_config"].values())
    assert abs(params["scene"]["air-density"] - 0.1) < 1e-6
    assert set(params["dyn_param"]) == {"wind", "air-density"}
    assert params["dyn_param"]["wind"][-1][1] == [0.0, 3.0, -0.0]
    assert params["dyn_param"]["air-density"][-1][1] == [0.5]
    print("Cloth NeXt multi-object Blender smoke passed")
finally:
    addon.unregister()
