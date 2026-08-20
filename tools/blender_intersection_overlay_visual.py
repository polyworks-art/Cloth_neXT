# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture a real VIEW_3D screenshot with Cloth NeXt diagnostics active."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bpy


values = sys.argv[sys.argv.index("--") + 1:]
parser = argparse.ArgumentParser()
parser.add_argument("--repo", type=Path, required=True)
parser.add_argument("--blend", type=Path, required=True)
parser.add_argument("--screenshot", type=Path, required=True)
parser.add_argument("--auto-fix-contact", action="store_true")
args = parser.parse_args(values)
sys.path.insert(0, str(args.repo))

from cloth_next.blender import registration, solver_test  # noqa: E402

registration.register()
bpy.ops.wm.open_mainfile(filepath=str(args.blend))
result, _stats = solver_test._revalidate_local_geometry(bpy.context)
assert result.degenerate_faces or result.violations
if args.auto_fix_contact:
    from cloth_next.ppf.resolver import (SolverResolutionContext,
                                         SolverResolver)
    from cloth_next.updater.install_paths import ManagedSolverPaths
    from cloth_next.updater.solver_registry import load_registry
    selected = load_registry(
        ManagedSolverPaths.default().registry_json).selected
    resolved = SolverResolver(solver_test._version_probe).resolve(
        SolverResolutionContext(selected_installation=selected))
    assert resolved is not None
    solver_test.resolve_solver = lambda _context: resolved
    solver_test.addon_preferences = lambda *_args: SimpleNamespace(
        selected_solver_installation_id=selected.installation_id)
    assert bpy.ops.clothnext.intersection_auto_fix("EXEC_DEFAULT") == {
        "FINISHED"}
    deadline = time.monotonic() + 120.0
    while solver_test._active_plan is not None and time.monotonic() < deadline:
        solver_test._pump_once()
        time.sleep(0.05)
    assert solver_test._active_plan is None
    result = solver_test.diagnostic_result()
    assert result.has_intersections and not result.has_degenerate_faces


def capture():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=3)
    screenshot = args.screenshot.resolve()
    bpy.ops.screen.screenshot(filepath=str(screenshot))
    assert screenshot.is_file()
    print("CLOTH_NEXT_OVERLAY_SCREENSHOT_PASS", screenshot)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(capture, first_interval=1.0)
