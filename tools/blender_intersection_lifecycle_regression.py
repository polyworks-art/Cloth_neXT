# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-Blender regression for geometry diagnostics and overlay lifecycle."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bpy


def _args():
    values = sys.argv[sys.argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--blend", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(values)


def _counts(result):
    return {
        "degenerates": len(result.degenerate_faces),
        "local_or_solver_intersections": int(result.detected_count),
        "presented": len(result.violations) + len(result.degenerate_faces),
    }


def _wait_for_worker(solver_test, timeout=120.0):
    deadline = time.monotonic() + timeout
    while solver_test._active_plan is not None and time.monotonic() < deadline:
        result = solver_test._pump_once()
        if result is None and solver_test._active_plan is None:
            break
        time.sleep(0.05)
    if solver_test._active_plan is not None:
        solver_test.request_cancel()
        raise TimeoutError("contact validation did not terminate")


def main():
    args = _args()
    sys.path.insert(0, str(args.repo))
    from cloth_next.blender import (intersection_overlay, registration,
                                    solver_test)

    registration.register()
    report = {"blender": bpy.app.version_string, "blend": str(args.blend)}
    try:
        # Source-tree registration has no installed AddonPreferences entry.
        # Point its otherwise normal resolver at the user's selected managed
        # installation without modifying Blender preferences or the registry.
        from cloth_next.updater.install_paths import ManagedSolverPaths
        from cloth_next.updater.solver_registry import load_registry
        from cloth_next.ppf.resolver import (SolverResolutionContext,
                                             SolverResolver)
        registry = load_registry(ManagedSolverPaths.default().registry_json)
        selected = registry.selected
        resolved = SolverResolver(solver_test._version_probe).resolve(
            SolverResolutionContext(selected_installation=selected))
        assert resolved is not None
        solver_test.resolve_solver = lambda _context: resolved
        solver_test.addon_preferences = lambda *_args: SimpleNamespace(
            selected_solver_installation_id=selected.installation_id,
            auto_launch_bake_window=False,
            telemetry_refresh_seconds=1.0,
            auto_cancel_high_ram=False,
            auto_cancel_ram_percent=90)
        bpy.ops.wm.open_mainfile(filepath=str(args.blend))
        assert intersection_overlay.presentation_diagnostics() == ()
        assert intersection_overlay._draw_handle is None
        assert intersection_overlay._label_handle is None

        # A real Bake entry must stop at the local gate when this fixture still
        # contains blocking geometry.  If it is already locally clean, cancel
        # the run immediately; the separate contact-only path is exercised
        # after Auto Fix below.
        start_error = ""
        try:
            solver_test.start_run(bpy.context)
        except solver_test.SceneValidationError as exc:
            start_error = str(exc)
        if solver_test._active_plan is not None:
            _wait_for_worker(solver_test)
        initial = solver_test.diagnostic_result()
        report["first_bake_error"] = start_error
        report["first_bake"] = _counts(initial)
        report["first_handlers"] = {
            "geometry": intersection_overlay._draw_handle is not None,
            "label": intersection_overlay._label_handle is not None,
        }
        if not (initial.violations or initial.degenerate_faces):
            local, _stats = solver_test._revalidate_local_geometry(bpy.context)
            report["direct_local_recheck"] = _counts(local)
            initial = local
        if not (initial.violations or initial.degenerate_faces):
            triangle = initial.snapshot.triangles[0]
            synthetic = solver_test.intersection_diagnostics.DegenerateFace(
                object_uuid=triangle.owner.object_uuid,
                object_name=triangle.owner.object_name,
                role=triangle.owner.role,
                combined_triangle_index=triangle.owner.combined_triangle_index,
                local_triangle_index=triangle.owner.local_triangle_index,
                source_polygon_index=triangle.owner.source_polygon_index,
                vertex_indices=triangle.vertex_indices,
                vertices=triangle.vertices)
            initial = solver_test.intersection_diagnostics.DiagnosticResult(
                snapshot=initial.snapshot, degenerate_faces=(synthetic,))
            intersection_overlay.set_diagnostic_session(initial)
            report["overlay_fixture"] = "synthetic diagnostic on real mesh"

        # Reproduce the stale-token condition: Blender owns neither callback,
        # while the add-on still holds both non-None opaque tokens.
        stale = (intersection_overlay._draw_handle,
                 intersection_overlay._label_handle)
        for handle in stale:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        intersection_overlay.set_diagnostic_session(initial)
        fresh = (intersection_overlay._draw_handle,
                 intersection_overlay._label_handle)
        assert all(handle is not None for handle in fresh)
        assert fresh != stale
        report["stale_handle_recovered"] = True

        # Ten publications must continuously replace one tracked pair.  Every
        # previous pair is explicitly retired by set_diagnostic_session().
        pairs = []
        for _cycle in range(10):
            intersection_overlay.set_diagnostic_session(initial)
            pair = (intersection_overlay._draw_handle,
                    intersection_overlay._label_handle)
            assert all(handle is not None for handle in pair)
            pairs.append(tuple(map(repr, pair)))
        report["handler_cycles"] = len(pairs)
        report["unique_handler_pairs"] = len(set(pairs))

        # Clear/set and save/reopen cover the cache/file lifecycle boundaries.
        intersection_overlay.clear()
        assert intersection_overlay._draw_handle is None
        assert intersection_overlay._label_handle is None
        intersection_overlay.set_diagnostic_session(initial)
        report["after_clear_handlers"] = {
            "geometry": intersection_overlay._draw_handle is not None,
            "label": intersection_overlay._label_handle is not None,
        }
        bpy.ops.wm.save_as_mainfile(
            filepath=str(args.blend), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(args.blend))
        assert intersection_overlay.presentation_diagnostics() == ()
        assert intersection_overlay._draw_handle is None
        assert intersection_overlay._label_handle is None
        reopened, _stats = solver_test._revalidate_local_geometry(bpy.context)
        report["after_reopen"] = _counts(reopened)
        report["after_reopen_handlers"] = {
            "geometry": intersection_overlay._draw_handle is not None,
            "label": intersection_overlay._label_handle is not None,
        }

        if reopened.degenerate_faces:
            outcome = bpy.ops.clothnext.intersection_auto_fix("EXEC_DEFAULT")
            report["auto_fix_operator"] = sorted(outcome)
            if solver_test._active_plan is not None:
                _wait_for_worker(solver_test)
            report["after_auto_fix"] = _counts(
                solver_test.diagnostic_result())
            report["after_auto_fix_handlers"] = {
                "geometry": intersection_overlay._draw_handle is not None,
                "label": intersection_overlay._label_handle is not None,
            }
            report["after_auto_fix_controller"] = (
                solver_test.shared_controller.snapshot().state.value)
            # A second explicit Bake must clear the old result first and then
            # republish the same contact-build truth with live handlers.
            try:
                solver_test.start_run(bpy.context)
            except solver_test.SceneValidationError as exc:
                report["repeated_bake_error"] = str(exc)
            if solver_test._active_plan is not None:
                _wait_for_worker(solver_test)
            repeated = solver_test.diagnostic_result()
            report["repeated_bake"] = _counts(repeated)
            report["repeated_bake_handlers"] = {
                "geometry": intersection_overlay._draw_handle is not None,
                "label": intersection_overlay._label_handle is not None,
            }

        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("CLOTH_NEXT_INTERSECTION_LIFECYCLE_PASS", json.dumps(report))
    finally:
        solver_test.shutdown()
        registration.unregister()


if __name__ == "__main__":
    main()
