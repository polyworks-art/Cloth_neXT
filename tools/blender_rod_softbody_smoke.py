# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender smoke for Rod Curve playback and Soft Body setup."""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
addon = importlib.import_module("cloth_next")
addon.register()
try:
    curve = bpy.data.curves.new("RodCurve", "CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(2)
    for point, co in zip(spline.bezier_points,
                         ((-1, 0, 1), (0, 0, 1), (1, 0, 1))):
        point.co = co
    rod = bpy.data.objects.new("Rod", curve)
    bpy.context.collection.objects.link(rod)
    bpy.context.view_layer.objects.active = rod
    rod.select_set(True)
    bpy.ops.clothnext.add_physics()
    bpy.ops.clothnext.set_object_type(role="ROD")
    assert rod.cloth_next.role == "ROD"
    assert rod.cloth_next.rod.linear_density == 1.0

    from cloth_next.bake.pc2 import write_pc2
    from cloth_next.blender import solver_test
    path = Path(tempfile.mkdtemp(prefix="cn-rod-smoke-")) / "cn_test_cloth_rod.pc2"
    frames = [((-1, 0, 1), (0, 0, 1), (1, 0, 1)),
              ((-1, 0, 0.9), (0, 0, 0.7), (1, 0, 0.9))]
    header = write_pc2(path, frames)
    plan = solver_test.RunPlan(None, None, frames[0],
        ((1,0,0,0),(0,1,0,0),(0,0,1,0),(0,0,0,1)), rod.name,
        path.parent, path, 2, frame_start=1, frame_end=2,
        deformable_role="ROD")
    solver_test._attach_playback(plan, header)
    action = curve.animation_data.action
    assert action is not None
    assert action.get("cloth_next_rod_action") is True

    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0)
    soft = bpy.context.object
    bpy.ops.clothnext.add_physics()
    bpy.ops.clothnext.set_object_type(role="SOFT_BODY")
    assert soft.cloth_next.role == "SOFT_BODY"
    assert soft.cloth_next.soft_body.tetrahedralizer == "FTETWILD"
    assert solver_test._non_manifold_edge_count(soft.data) == 0
    print("Cloth NeXt Rod/Soft Body Blender smoke passed")
finally:
    addon.unregister()
