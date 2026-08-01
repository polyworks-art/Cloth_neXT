# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Blender production-UI Newton Live Preview acceptance gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=("timeline", "crash", "invalidation",
                                           "cache"),
                        default="timeline")
    return parser.parse_args(values)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _positions(obj):
    return tuple(tuple(float(value) for value in vertex.co)
                 for vertex in obj.data.vertices)


def _digest(value):
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def _play_operator():
    window = bpy.context.window_manager.windows[0]
    area = next((item for item in window.screen.areas
                 if item.type == "DOPESHEET_EDITOR"), None)
    if area is None:
        area = next((item for item in window.screen.areas
                     if item.type == "VIEW_3D"), None)
    if area is None:
        raise RuntimeError("no Blender timeline-capable area is available")
    region = next((item for item in area.regions if item.type == "WINDOW"), None)
    with bpy.context.temp_override(window=window, screen=window.screen,
                                   area=area, region=region):
        return set(bpy.ops.screen.animation_play())


def main():
    args = _args()
    sys.path.insert(0, str(args.repo))
    os.environ["CLOTHNEXT_NEWTON_PYTHON"] = str(args.python)
    from cloth_next.blender import newton_preview, registration
    registration.register()

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=9, y_subdivisions=9,
                                    size=2.0, location=(0.0, 0.0, 1.5))
    cloth = bpy.context.object
    cloth.name = "Newton UI Gate Cloth"
    cloth.cloth_next.enabled = True
    cloth.cloth_next.role = "CLOTH"
    cloth.cloth_next.persistent_export_id = "newton-ui-gate-cloth"
    cloth.cloth_next.bake_start = 1
    cloth.cloth_next.bake_end = 8
    cloth.cloth_next.material.surface_weight = 0.2
    cloth.cloth_next.material.stretch_resistance = 1000.0
    cloth.cloth_next.material.bend_resistance = 5.0
    cloth.cloth_next.collision.collision_gap = 0.005

    group = cloth.vertex_groups.new(name="Newton Pins")
    top = [vertex.index for vertex in cloth.data.vertices
           if vertex.co.y > 0.99]
    group.add((top[0], top[-1]), 1.0, "REPLACE")
    cloth.cloth_next.pinning_enabled = True
    cloth.cloth_next.pin_group = group.name
    cloth.cloth_next.pin_mode = "STATIC"

    scene = bpy.context.scene
    scene.name = "Newton Live Preview UI Gate"
    scene.frame_start = 1; scene.frame_end = 8
    scene.render.fps = 24; scene.render.fps_base = 1.0
    scene.frame_set(1)
    settings = scene.cloth_next_newton_preview
    settings.quality = "FAST"
    settings.enable_self_contact = False
    original_positions = _positions(cloth)
    original_digest = _digest(original_positions)
    observations = {
        "status_history": [], "worker_pids": [], "play_operator": None,
        "pause_operator": None, "source_digest_before": original_digest,
    }
    frame_data = {}
    deadline = time.monotonic() + 300.0
    phase = "enable"
    stable_wait = 0

    # This assignment invokes the exact BoolProperty update used by the visible
    # checkbox; no private start function or worker shortcut is called.
    settings.enabled = True

    def drive():
        nonlocal phase, stable_wait
        try:
            if time.monotonic() > deadline:
                raise TimeoutError(f"Newton UI gate timed out during {phase}")
            status = str(settings.status)
            if not observations["status_history"] or observations["status_history"][-1] != status:
                observations["status_history"].append(status)
            pid = newton_preview._session.client.pid if newton_preview._session.client else None
            if pid and pid not in observations["worker_pids"]:
                observations["worker_pids"].append(pid)

            if phase == "enable":
                if status == "Preview Error":
                    raise RuntimeError(settings.status_detail)
                if status != "Live" or newton_preview._session.last_applied_frame != 1:
                    return 0.05
                preview = newton_preview._session.capture.preview
                frame_data["initial"] = _positions(preview)
                if args.mode == "crash":
                    observations["crashed_pid"] = newton_preview._session.client.pid
                    newton_preview._session.client.process.kill()
                    phase = "crash_wait"
                    return 0.05
                if args.mode == "invalidation":
                    observations["initial_worker_pid"] = (
                        newton_preview._session.client.pid)
                    cloth.cloth_next.material.bend_resistance += 1.0
                    phase = "invalidation_wait"
                    return 0.05
                if args.mode == "cache":
                    observations["first_capture_metrics"] = dict(
                        newton_preview._session.capture.capture_metrics)
                    settings.enabled = False
                    phase = "cache_first_disabled"
                    return 0.05
                observations["play_operator"] = sorted(_play_operator())
                phase = "playing"
                return 0.05

            if phase == "crash_wait":
                if settings.status != "Preview Error":
                    return 0.05
                observations["crash_status"] = settings.status
                observations["crash_detail"] = settings.status_detail
                observations["source_restored_after_crash"] = not cloth.hide_viewport
                observations["preview_count_after_crash"] = sum(
                    1 for obj in bpy.data.objects
                    if bool(obj.get("cloth_next_newton_preview_owned", False)))
                settings.enabled = True
                phase = "restart"
                return 0.05

            if phase == "invalidation_wait":
                if settings.status != "Scene Changed":
                    return 0.05
                observations["invalidation_status"] = settings.status
                observations["invalidation_detail"] = settings.status_detail
                observations["worker_pid_after_change"] = (
                    newton_preview._session.client.pid)
                settings.enabled = False
                phase = "invalidation_disabled"
                return 0.05

            if phase == "cache_first_disabled":
                if newton_preview._session.state.value != "DISABLED":
                    return 0.05
                cached = newton_preview._mesh_capture_cache.get(str(cloth.name))
                observations["cache_entries_before_second"] = len(
                    newton_preview._mesh_capture_cache)
                observations["cache_key_matches_before_second"] = bool(
                    cached is not None and cached[0] ==
                    newton_preview._mesh_capture_key(cloth))
                settings.quality = "HIGH"
                settings.enabled = True
                phase = "cache_second"
                return 0.05

            if phase == "cache_second":
                if settings.status == "Preview Error":
                    raise RuntimeError(settings.status_detail)
                if (settings.status != "Live"
                        or newton_preview._session.last_applied_frame != 1):
                    return 0.05
                observations["second_capture_metrics"] = dict(
                    newton_preview._session.capture.capture_metrics)
                settings.enabled = False
                phase = "cache_second_disabled"
                return 0.05

            if phase == "cache_second_disabled":
                if newton_preview._session.state.value != "DISABLED":
                    return 0.05
                second = observations["second_capture_metrics"]
                report = {
                    "gate": "newton_mesh_capture_cache", "result": "passed",
                    "newton_version": "1.4.0", "warp_version": "1.15.0",
                    **observations,
                    "quality_change_reused_geometry": (
                        second.get("newton_mesh_capture_hits") == 1
                        and second.get("newton_mesh_capture_evaluations") == 0),
                    "source_unchanged": _digest(_positions(cloth)) == original_digest,
                    "source_visibility_restored": not cloth.hide_viewport,
                }
                if not all((report["quality_change_reused_geometry"],
                            report["source_unchanged"],
                            report["source_visibility_restored"])):
                    raise AssertionError(
                        f"Newton mesh cache gate invariant failed: {report}")
                _write(args.report, report)
                bpy.ops.wm.quit_blender()
                return None

            if phase == "invalidation_disabled":
                if newton_preview._session.state.value != "DISABLED":
                    return 0.05
                report = {
                    "gate": "newton_live_preview_invalidation",
                    "result": "passed", "newton_version": "1.4.0",
                    "warp_version": "1.15.0", **observations,
                    "same_worker_before_cleanup": (
                        observations.get("initial_worker_pid") ==
                        observations.get("worker_pid_after_change")),
                    "source_unchanged": _digest(_positions(cloth)) == original_digest,
                    "source_visibility_restored": not cloth.hide_viewport,
                    "preview_count_after_disable": sum(
                        1 for obj in bpy.data.objects
                        if bool(obj.get("cloth_next_newton_preview_owned", False))),
                }
                required = (report["same_worker_before_cleanup"],
                            report["source_unchanged"],
                            report["source_visibility_restored"],
                            report["preview_count_after_disable"] == 0)
                if not all(required):
                    raise AssertionError(
                        f"Newton invalidation gate invariant failed: {report}")
                _write(args.report, report)
                bpy.ops.wm.quit_blender()
                return None

            if phase == "restart":
                if settings.status == "Preview Error":
                    raise RuntimeError(f"Newton preview restart failed: {settings.status_detail}")
                if (settings.status != "Live"
                        or newton_preview._session.last_applied_frame != 1
                        or len(observations["worker_pids"]) < 2):
                    return 0.05
                observations["restart_pid"] = newton_preview._session.client.pid
                settings.enabled = False
                phase = "crash_disabled"
                return 0.05

            if phase == "crash_disabled":
                if newton_preview._session.state.value != "DISABLED":
                    return 0.05
                source_after = _positions(cloth)
                report = {
                    "gate": "newton_live_preview_worker_crash",
                    "result": "passed", "newton_version": "1.4.0",
                    "warp_version": "1.15.0", **observations,
                    "blender_survived": True,
                    "source_unchanged": _digest(source_after) == original_digest,
                    "source_visibility_restored": not cloth.hide_viewport,
                    "restart_used_new_worker": (
                        observations.get("restart_pid")
                        != observations.get("crashed_pid")),
                }
                required = (report["source_unchanged"],
                            report["source_visibility_restored"],
                            report["restart_used_new_worker"],
                            observations.get("source_restored_after_crash"),
                            observations.get("preview_count_after_crash") == 0)
                if not all(required):
                    raise AssertionError(f"Newton crash gate invariant failed: {report}")
                _write(args.report, report)
                bpy.ops.wm.quit_blender()
                return None

            if phase == "playing":
                if scene.frame_current < 4:
                    return 0.05
                observations["pause_operator"] = sorted(_play_operator())
                target = int(scene.frame_current)
                scene.frame_set(target)
                observations["play_target"] = target
                phase = "pause_wait"
                return 0.05

            if phase == "pause_wait":
                if newton_preview._session.last_applied_frame != observations["play_target"]:
                    return 0.05
                preview = newton_preview._session.capture.preview
                frame_data["played"] = _positions(preview)
                scene.frame_set(8)
                phase = "forward"
                return 0.05

            if phase == "forward":
                if newton_preview._session.last_applied_frame != 8:
                    return 0.05
                frame_data["forward"] = _positions(
                    newton_preview._session.capture.preview)
                scene.frame_set(3)
                phase = "backward"
                return 0.05

            if phase == "backward":
                if newton_preview._session.last_applied_frame != 3:
                    return 0.05
                frame_data["backward"] = _positions(
                    newton_preview._session.capture.preview)
                scene.frame_set(1)
                phase = "start"
                return 0.05

            if phase == "start":
                if newton_preview._session.last_applied_frame != 1:
                    return 0.05
                frame_data["restored_start"] = _positions(
                    newton_preview._session.capture.preview)
                observations["diagnostics"] = {
                    key: value for key, value in
                    newton_preview._session.status_data.items()
                    if key.startswith("newton_")}
                settings.enabled = False
                phase = "disabled"
                return 0.05

            if phase == "disabled":
                if newton_preview._session.state.value != "DISABLED":
                    return 0.05
                source_after = _positions(cloth)
                preview_objects = [obj.name for obj in bpy.data.objects
                                   if bool(obj.get("cloth_next_newton_preview_owned", False))]
                initial = frame_data["initial"]
                played = frame_data["played"]
                restored = frame_data["restored_start"]
                report = {
                    "gate": "newton_live_preview_ui", "result": "passed",
                    "newton_version": "1.4.0", "warp_version": "1.15.0",
                    **observations,
                    "frame_digests": {name: _digest(value)
                                      for name, value in frame_data.items()},
                    "frame_min_z": {name: min(point[2] for point in value)
                                    for name, value in frame_data.items()},
                    "one_persistent_worker": len(observations["worker_pids"]) == 1,
                    "viewport_changed": _digest(played) != _digest(initial),
                    "pinned_vertices_stable": all(
                        played[index] == initial[index] for index in (90, 99)),
                    "rewind_changed_from_forward": (
                        _digest(frame_data["backward"])
                        != _digest(frame_data["forward"])),
                    "start_exactly_restored": _digest(restored) == _digest(initial),
                    "source_digest_after": _digest(source_after),
                    "source_unchanged": _digest(source_after) == original_digest,
                    "source_visibility_restored": cloth.hide_viewport is False,
                    "preview_objects_after_disable": preview_objects,
                }
                required = (report["one_persistent_worker"], report["viewport_changed"],
                            report["rewind_changed_from_forward"],
                            report["start_exactly_restored"],
                            report["pinned_vertices_stable"], report["source_unchanged"],
                            report["source_visibility_restored"], not preview_objects)
                if not all(required):
                    raise AssertionError(f"Newton UI gate invariant failed: {report}")
                _write(args.report, report)
                bpy.ops.wm.quit_blender()
                return None
        except Exception as exc:
            try: newton_preview.stop()
            except Exception: pass
            _write(args.report, {"gate": "newton_live_preview_ui",
                                 "result": "failed", "phase": phase,
                                 "error": str(exc), "traceback": traceback.format_exc(),
                                 **observations})
            bpy.ops.wm.quit_blender()
            return None
        return 0.05

    bpy.app.timers.register(drive, first_interval=0.05)


if __name__ == "__main__":
    main()
