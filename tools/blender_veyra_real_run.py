# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive real-file VEYRA end-to-end measurement for Blender."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import bpy
import bmesh


values = sys.argv[sys.argv.index("--") + 1:]
parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--blend", type=Path, required=True)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--global-weld-object", default="")
parser.add_argument("--global-weld-threshold", type=float, default=0.0)
args = parser.parse_args(values)
sys.path.insert(0, str(args.repo))
print("CLOTH_NEXT_VEYRA_REAL_START", args.blend, flush=True)

from cloth_next.blender import (companion_manager, intersection_overlay,
                                registration, solver_test)
from cloth_next.bake.status import BakeState
from cloth_next.veyra.model import VeyraStep
from cloth_next.veyra.regions import analysis_dict, build_regions


def counts(result):
    return {"degenerates": len(result.degenerate_faces),
            "intersections": int(result.detected_count),
            "detailed": int(result.detailed_count),
            "mapped": int(result.mapped_count),
            "unmapped": int(result.unmapped_count),
            "overlay": intersection_overlay.mapped_count()}


def companion_window():
    result = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                      ctypes.c_void_p)
    def visit(hwnd, _lparam):
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buffer, length + 1)
            if buffer.value in {"Cloth NeXt Bake", "Cloth NeXt Veyra"}:
                pid = ctypes.c_ulong()
                ctypes.windll.user32.GetWindowThreadProcessId(
                    hwnd, ctypes.byref(pid))
                result.append((pid.value, buffer.value))
        return True
    ctypes.windll.user32.EnumWindows(callback_type(visit), 0)
    return result[0] if result else (None, "")


def region_input(result):
    pairs = [tuple(map(int, item.combined_pair))
             for item in result.self_intersections
             if len(item.elements) == 2
             and item.elements[0].object_uuid == item.elements[1].object_uuid]
    involved_objects = {
        item.elements[0].object_uuid for item in result.self_intersections
        if len(item.elements) == 2
        and item.elements[0].object_uuid == item.elements[1].object_uuid}
    triangles = []
    for item in result.snapshot.triangles if result.snapshot else ():
        if item.owner.object_uuid not in involved_objects:
            continue
        triangles.append({
            "object_uuid": item.owner.object_uuid,
            "triangle_index": item.owner.combined_triangle_index,
            "vertex_indices": item.vertex_indices,
            "vertices": item.vertices,
        })
    return {
        "authoritative_total": result.detected_count,
        "detailed_count": result.detailed_count,
        "mapped_count": result.mapped_count,
        "pairs": pairs, "triangles": triangles,
    }


registration.register()

from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.solver_overlay import apply_managed_solver_overlay
from cloth_next.updater.install_paths import ManagedSolverPaths
from cloth_next.updater.solver_registry import load_registry

selected = load_registry(ManagedSolverPaths.default().registry_json).selected
apply_managed_solver_overlay(selected.root)
resolved = SolverResolver(solver_test._version_probe).resolve(
    SolverResolutionContext(selected_installation=selected))
assert resolved is not None
solver_test.resolve_solver = lambda _context: resolved
solver_test.addon_preferences = lambda *_args: SimpleNamespace(
    selected_solver_installation_id=selected.installation_id,
    auto_launch_bake_window=True, telemetry_refresh_seconds=1.0,
    auto_cancel_high_ram=False, auto_cancel_ram_percent=90)

report = {"blender": bpy.app.version_string, "blend": str(args.blend),
          "work_sha_before": hashlib.sha256(args.blend.read_bytes()).hexdigest(),
          "states": [], "steps": [], "responsive_ticks_during_planning": 0,
          "frame_simulation_started": False, "timings": {}}
phase = ["start"]
deadline = time.monotonic() + 1200.0
veyra_started = [None]
contact_started = [None]
bake_terminal_at = [None]

original_apply = solver_test._apply_veyra_plan
original_revalidate = solver_test._revalidate_local_geometry
original_contact = solver_test._continue_contact_validation
original_contact_result = solver_test._handle_veyra_contact_result


def measured_apply(*positional, **keywords):
    if not report["steps"] or report["steps"][-1] != VeyraStep.APPLYING_REPAIRS.value:
        report["steps"].append(VeyraStep.APPLYING_REPAIRS.value)
    plan = positional[1] if len(positional) > 1 else keywords.get("plan")
    if plan is not None:
        report["repairs_planned"] = {
            "attempted": plan.attempted_count,
            "planned": plan.planned_count,
            "skipped": plan.skipped_count,
            "displacements": len(plan.displacements),
            "welds": len(plan.welds),
            "weld_vertices": sum(len(item.vertex_indices) for item in plan.welds),
            "skip_reasons": dict(plan.skip_reasons),
        }
    started = time.monotonic()
    try:
        result = original_apply(*positional, **keywords)
        changed, moved, welded = result
        report["repairs_applied"] = {
            "objects": sorted(item.name for item in changed),
            "displacements": moved, "welds": welded,
            "total_operations": moved + welded,
        }
        return result
    finally:
        report["timings"]["apply"] = time.monotonic() - started


def measured_revalidate(*positional, **keywords):
    if veyra_started[0] is not None and (
            not report["steps"] or report["steps"][-1] !=
            VeyraStep.REVALIDATING_GEOMETRY.value):
        report["steps"].append(VeyraStep.REVALIDATING_GEOMETRY.value)
    started = time.monotonic()
    try: return original_revalidate(*positional, **keywords)
    finally:
        if veyra_started[0] is not None:
            report["timings"]["revalidate"] = time.monotonic() - started


def measured_contact(*positional, **keywords):
    if keywords.get("veyra"):
        contact_started[0] = time.monotonic()
        if (not report["steps"] or report["steps"][-1] !=
                VeyraStep.VALIDATING_CONTACTS.value):
            report["steps"].append(VeyraStep.VALIDATING_CONTACTS.value)
    return original_contact(*positional, **keywords)


solver_test._apply_veyra_plan = measured_apply
solver_test._revalidate_local_geometry = measured_revalidate
solver_test._continue_contact_validation = measured_contact


def measured_contact_result(result):
    if ("authoritative_baseline" not in report
            and result.has_intersections and result.snapshot is not None):
        value = region_input(result)
        path = args.report.with_name(f"{args.report.stem}-baseline-input.json")
        path.write_text(json.dumps(value), encoding="utf-8")
        report["authoritative_baseline"] = counts(result)
        report["authoritative_baseline_input"] = str(path)
    return original_contact_result(result)


solver_test._handle_veyra_contact_result = measured_contact_result


def apply_global_weld_measurement():
    if not args.global_weld_object:
        return
    obj = bpy.data.objects.get(args.global_weld_object)
    if obj is None or obj.type != "MESH":
        raise RuntimeError(
            f"global weld measurement object {args.global_weld_object!r} missing")
    mesh = obj.data
    before = {"vertices": len(mesh.vertices), "edges": len(mesh.edges),
              "polygons": len(mesh.polygons)}
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bmesh.ops.remove_doubles(
            bm, verts=list(bm.verts), dist=args.global_weld_threshold)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update(); solver_test.validation_state.forget(obj)
    report["diagnostic_global_weld"] = {
        "object": obj.name, "local_threshold": args.global_weld_threshold,
        "world_threshold_at_uniform_scale": (
            args.global_weld_threshold * abs(float(obj.scale.x))),
        "before": before,
        "after": {"vertices": len(mesh.vertices), "edges": len(mesh.edges),
                  "polygons": len(mesh.polygons)},
    }


def finish(error=""):
    if error: report["error"] = error
    terminal_snapshot = solver_test.shared_controller.snapshot()
    report["terminal"] = {
        "state": terminal_snapshot.state.value,
        "summary": terminal_snapshot.error_summary,
        "message": terminal_snapshot.status_message,
        "details": terminal_snapshot.error_details,
        "code": terminal_snapshot.error_code,
    }
    current = solver_test.diagnostic_result()
    report["diagnostics_after"] = counts(current)
    if current.has_intersections and current.snapshot is not None:
        region_value = region_input(current)
        region_path = args.report.with_name(
            f"{args.report.stem}-region-input.json")
        region_path.write_text(json.dumps(region_value), encoding="utf-8")
        report["shorts_region_input"] = str(region_path)
        report["shorts_region_analysis"] = analysis_dict(
            build_regions(region_value))
    report["pid_after"], report["title"] = companion_window()
    report["process_reused"] = (
        report.get("pid_before") is not None
        and report.get("pid_before") == report.get("pid_after"))
    job_id = report.get("veyra_job_id", "")
    metrics = companion_manager.veyra_metrics(job_id)
    report["companion_metrics"] = metrics
    report["region_metrics"] = solver_test.veyra_region_metrics()
    progress = metrics.get("progress", [])
    plan_seconds = metrics.get("planning_seconds")
    if plan_seconds is not None:
        analyze_end = max((float(item.get("elapsed", 0.0) or 0.0)
                           for item in progress
                           if item.get("step") ==
                           VeyraStep.ANALYZING_DIAGNOSTICS.value), default=0.0)
        report["timings"]["analyze"] = analyze_end
        report["timings"]["plan"] = max(0.0, float(plan_seconds)-analyze_end)
    if contact_started[0] is not None:
        report["timings"]["contact_validation"] = (
            time.monotonic() - contact_started[0])
    if veyra_started[0] is not None:
        report["timings"]["total_veyra"] = time.monotonic()-veyra_started[0]
    try:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.blend),
                                   check_existing=False)
    except Exception as exc:
        report["save_error"] = str(exc)
    report["work_sha_after"] = hashlib.sha256(args.blend.read_bytes()).hexdigest()
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("CLOTH_NEXT_VEYRA_REAL_PASS", json.dumps(report))
    companion_manager.shutdown(); solver_test.shutdown(); registration.unregister()
    bpy.ops.wm.quit_blender()
    return None


def tick():
    try:
        if time.monotonic() > deadline:
            return finish("real VEYRA run timed out")
        snapshot = solver_test.shared_controller.snapshot()
        state = snapshot.state.value
        if not report["states"] or report["states"][-1] != state:
            report["states"].append(state)
        if snapshot.state is BakeState.SIMULATING:
            report["frame_simulation_started"] = True
        if snapshot.veyra_step is not None:
            step = snapshot.veyra_step.value
            if not report["steps"] or report["steps"][-1] != step:
                report["steps"].append(step)
            if snapshot.veyra_step in {
                    VeyraStep.ANALYZING_DIAGNOSTICS,
                    VeyraStep.SOLVING_REPAIR_PLAN}:
                report["responsive_ticks_during_planning"] += 1

        if phase[0] == "start":
            phase[0] = "bake"
            try:
                report["bake_operator"] = sorted(
                    bpy.ops.clothnext.bake("EXEC_DEFAULT"))
            except RuntimeError as exc:
                # Blender's Python operator bridge raises for a reported
                # validation cancellation; the retained diagnostics are the
                # expected Bake outcome and remain authoritative.
                report["bake_operator"] = ["CANCELLED"]
                report["bake_error"] = str(exc)
        elif phase[0] == "bake":
            if (snapshot.state in {BakeState.ERROR, BakeState.FINISHED,
                                   BakeState.CANCELLED}
                    and not solver_test.run_active()
                    and solver_test._pending_plan is None):
                before = solver_test.diagnostic_result()
                if not (before.violations or before.degenerate_faces):
                    before, _ = solver_test._revalidate_local_geometry(
                        bpy.context)
                report["diagnostics_before"] = counts(before)
                pid, title = companion_window()
                if pid is None:
                    if bake_terminal_at[0] is None:
                        bake_terminal_at[0] = time.monotonic()
                    if time.monotonic() - bake_terminal_at[0] < 10.0:
                        return .05
                report["pid_before"], report["title_before"] = pid, title
                phase[0] = "veyra"
                veyra_started[0] = time.monotonic()
                apply_global_weld_measurement()
                report["auto_fix_operator"] = sorted(
                    bpy.ops.clothnext.intersection_auto_fix("EXEC_DEFAULT"))
                report["veyra_job_id"] = (
                    solver_test.shared_controller.snapshot().job_id)
        elif phase[0] == "veyra":
            terminal = snapshot.state in {
                BakeState.ERROR, BakeState.FINISHED, BakeState.CANCELLED}
            region_active = bool(
                solver_test.veyra_region_metrics().get("active", False))
            if (terminal and not solver_test.run_active()
                    and solver_test._active_plan is None
                    and not region_active):
                return finish()
            if snapshot.state is BakeState.ERROR and not solver_test.run_active():
                if bake_terminal_at[0] is None:
                    bake_terminal_at[0] = time.monotonic()
                if time.monotonic() - bake_terminal_at[0] >= 2.0:
                    return finish("VEYRA remained active after terminal ERROR")
        return .05
    except Exception as exc:
        import traceback
        report["traceback"] = traceback.format_exc()
        return finish(str(exc))


bpy.app.timers.register(tick, first_interval=.2)
