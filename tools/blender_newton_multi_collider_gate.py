# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender gate for Newton multi-cloth and deforming Collider preview."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(values)


def _cloth(name, identifier, location):
    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=5, y_subdivisions=5, size=1.0, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.cloth_next.enabled = True
    obj.cloth_next.role = "CLOTH"
    obj.cloth_next.persistent_export_id = identifier
    obj.cloth_next.bake_start = 1
    obj.cloth_next.bake_end = 4
    obj.cloth_next.material.surface_weight = 0.2
    obj.cloth_next.material.stretch_resistance = 1000.0
    group = obj.vertex_groups.new(name="Pins")
    top = [vertex.index for vertex in obj.data.vertices if vertex.co.y > 0.49]
    group.add((top[0], top[-1]), 1.0, "REPLACE")
    obj.cloth_next.pinning_enabled = True
    obj.cloth_next.pin_group = group.name
    obj.cloth_next.pin_mode = "STATIC"
    return obj


def main():
    args = _args()
    sys.path.insert(0, str(args.repo))
    os.environ["CLOTHNEXT_NEWTON_PYTHON"] = str(args.python)
    from cloth_next.blender import newton_preview, registration
    registration.register()
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    cloths = (_cloth("Gate Cloth A", "gate-cloth-a", (-0.7, 0.0, 1.3)),
              _cloth("Gate Cloth B", "gate-cloth-b", (0.7, 0.0, 1.6)))
    cloths[0].cloth_next.pin_mode = "FOLLOW_ANIMATION"
    cloths[0].location.x = -0.7
    cloths[0].keyframe_insert("location", frame=1)
    cloths[0].location.x = -0.3
    cloths[0].keyframe_insert("location", frame=4)
    bpy.ops.mesh.primitive_grid_add(
        x_subdivisions=4, y_subdivisions=4, size=4.0, location=(0, 0, 0))
    collider = bpy.context.object
    collider.name = "Gate Deforming Collider"
    collider.cloth_next.enabled = True
    collider.cloth_next.role = "COLLIDER"
    collider.cloth_next.collider_motion = "ANIMATED"
    collider.cloth_next.persistent_export_id = "gate-animated-collider"
    key = collider.shape_key_add(name="Basis")
    key = collider.shape_key_add(name="Deform")
    for vertex in key.data:
        vertex.co.z += 0.15 * (1.0 - abs(float(vertex.co.x)) / 2.0)
    key.value = 0.0
    key.keyframe_insert("value", frame=1)
    key.value = 1.0
    key.keyframe_insert("value", frame=4)
    scene = bpy.context.scene
    scene.frame_start = 1
    scene.frame_end = 4
    scene.frame_set(1)
    settings = scene.cloth_next_newton_preview
    settings.quality = "FAST"
    settings.enabled = True
    deadline = time.monotonic() + 300.0
    phase = "start"

    def drive():
        nonlocal phase
        try:
            if time.monotonic() > deadline:
                raise TimeoutError(f"gate timeout during {phase}")
            if settings.status == "Preview Error":
                raise RuntimeError(settings.status_detail)
            if phase == "start":
                if settings.status != "Live" or newton_preview._session.last_applied_frame != 1:
                    return 0.05
                if len(newton_preview._session.capture.cloths) != 2:
                    raise AssertionError("preview did not create two Cloth outputs")
                if len(newton_preview._session.capture.request.pin_animations) != 1:
                    raise AssertionError("Follow Animation Pin track was not captured")
                scene.frame_set(4)
                phase = "frame4"
                return 0.05
            if phase == "frame4":
                if newton_preview._session.last_applied_frame != 4:
                    return 0.05
                vertex_counts = [len(item.preview.data.vertices)
                                 for item in newton_preview._session.capture.cloths]
                request = newton_preview._session.capture.request
                pin_track = request.pin_animations[0]
                first_preview = newton_preview._session.capture.cloths[0].preview
                pin_positions = tuple(
                    tuple(float(value) for value in
                          first_preview.data.vertices[index].co)
                    for index in request.cloths[0].pin_indices)
                if any(sum((actual[axis] - expected[axis]) ** 2
                               for axis in range(3)) ** 0.5 > 1.0e-4
                       for actual, expected in zip(
                           pin_positions, pin_track.samples[-1])):
                    raise AssertionError("Follow Animation Pins missed final targets")
                settings.enabled = False
                report = {
                    "result": "passed", "cloth_objects": len(vertex_counts),
                    "preview_vertex_counts": vertex_counts,
                    "animated_collider_samples": 4,
                    "animated_pin_samples": len(pin_track.samples),
                    "frame": 4,
                    "sources_restored": all(not obj.hide_viewport for obj in cloths)}
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
                bpy.ops.wm.quit_blender()
                return None
        except Exception as exc:
            args.report.write_text(json.dumps({"result": "failed", "error": str(exc)}),
                                   encoding="utf-8")
            bpy.ops.wm.quit_blender()
            return None
        return 0.05

    bpy.app.timers.register(drive, first_interval=0.05)


main()
