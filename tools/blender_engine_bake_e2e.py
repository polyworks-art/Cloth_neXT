# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Real Blender Bake through the selected engine (AETHER or GAIA).

    blender --background --factory-startup --python tools/blender_engine_bake_e2e.py -- \
        --module bl_ext.user_default.cloth_next --engine GAIA --output <dir> \
        [--scene contact|collider] [--reproducible] [--repeat 2] [--cancel-after 3]

The engine is chosen through the Preferences Engine property, exactly as a
person would, so registry selection, engine identity, GAIA file verification
and the solver environment are all exercised. Two cloth sheets falling onto
each other give inter-cloth contact without collision meshes (the scope the
deterministic path covers); ``collider`` drapes a sheet over a sphere.
Each Bake's PC2 files are hashed; playback is compared against the PC2.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time

import addon_utils
import bpy

SOLVER_IMAGES = ("ppf-cts-server.exe", "ppf-contact-solver.exe")


def solver_processes() -> int:
    output = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return sum(1 for line in output.splitlines() if any(name in line for name in SOLVER_IMAGES))


def build_scene(kind: str, first: int, last: int, cache: Path) -> list:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    cloths = []
    sheets = {"contact": (("ContactSheetLower", 1.0, True), ("ContactSheetUpper", 1.25, False)),
              "pinned": (("PinnedSheet", 1.0, True),)}.get(kind, (("DrapeSheet", 1.2, False),))
    for name, height, pinned in sheets:
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=24, y_subdivisions=24, size=1.0,
                                        location=(0.0, 0.0, height))
        cloth = bpy.context.object
        cloth.name = name
        bpy.ops.clothnext.add_physics()
        cloth.cloth_next.bake_start, cloth.cloth_next.bake_end = first, last
        cloth.cloth_next.cache_directory = str(cache)
        if pinned:
            group = cloth.vertex_groups.new(name="Pins")
            coords = [(v.co.x, v.co.y) for v in cloth.data.vertices]
            corners = sorted({min(range(len(coords)), key=lambda i: sx * coords[i][0] + sy * coords[i][1])
                              for sx in (-1, 1) for sy in (-1, 1)})
            group.add(corners, 1.0, "REPLACE")
            cloth["pinned_indices"] = corners
            cloth.cloth_next.pinning_enabled = True
            cloth.cloth_next.pin_group = group.name
            cloth.cloth_next.pin_mode = "STATIC"
        cloths.append(cloth)
    if kind == "collider":
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.35, location=(0.0, 0.0, 0.6))
        sphere = bpy.context.object
        sphere.name = "DrapeSphere"
        bpy.ops.clothnext.add_physics()
        bpy.ops.clothnext.set_object_type(role="COLLIDER")
    return cloths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--engine", choices=("AETHER", "GAIA"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene", choices=("contact", "collider", "pinned"), default="contact")
    parser.add_argument("--reproducible", action="store_true")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--cancel-after", type=float)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    args.output.mkdir(parents=True, exist_ok=True)
    addon_utils.enable(args.module, default_set=True)
    solver_test = importlib.import_module(f"{args.module}.blender.solver_test")
    try:
        engines = importlib.import_module(f"{args.module}.updater.engines")
    except ModuleNotFoundError:
        engines = None
    pc2 = importlib.import_module(f"{args.module}.bake.pc2")
    prefs = bpy.context.preferences.addons[args.module].preferences
    prefs.auto_launch_bake_window = False
    if hasattr(prefs, "solver_engine"):  # older add-ons (baseline runs) have no engine property
        prefs.solver_engine = args.engine
        prefs.gaia_reproducible_bake = args.reproducible
    report = {"engine_requested": args.engine, "scene": args.scene, "reproducible": args.reproducible,
              "blender": bpy.app.version_string, "runs": []}
    resolved = solver_test.resolve_solver(bpy.context)
    report["installation"] = resolved.installation.display_name if resolved.installation else None
    report["engine_resolved"] = (engines.engine_of(resolved.installation) if engines
                                 else "AETHER (baseline add-on)")
    report["solver_environment"] = dict(getattr(resolved, "solver_environment", ()))
    assert engines is None or report["engine_resolved"] == args.engine, report
    before = solver_processes()
    ok = True
    for run in range(args.repeat):
        cache = args.output / f"run-{run + 1}"
        cloths = build_scene(args.scene, 1, args.frames, cache)
        started = time.monotonic()
        assert bpy.ops.clothnext.bake("EXEC_DEFAULT") in ({"FINISHED"}, {"RUNNING_MODAL"})
        cancelled = False
        deadline = started + args.timeout
        while solver_test.shared_controller.snapshot().active:
            if args.cancel_after and not cancelled and time.monotonic() - started > args.cancel_after:
                solver_test.request_cancel()
                cancelled = True
            if time.monotonic() > deadline:
                solver_test.request_cancel()
                raise TimeoutError("Bake did not finish in time")
            if solver_test._pin_capture is not None:
                solver_test._pin_capture_pump()
            solver_test._startup_pump()
            solver_test._pump()
            time.sleep(0.02)
        snapshot = solver_test.shared_controller.snapshot()
        entry = {"run": run + 1, "state": snapshot.state.value, "error": snapshot.error_summary,
                 "seconds": round(time.monotonic() - started, 1), "pc2": {}}
        if args.cancel_after:
            time.sleep(3.0)
            entry["solver_processes_after_cancel"] = solver_processes() - before
            ok = ok and snapshot.state.value in ("CANCELLED", "IDLE") and entry["solver_processes_after_cancel"] == 0
            report["runs"].append(entry)
            break
        ok = ok and snapshot.state.value == "FINISHED"
        for cloth in cloths:
            modifier = next(m for m in cloth.modifiers
                            if solver_test.is_cloth_next_playback_modifier(cloth, m) and m.filepath)
            path = Path(bpy.path.abspath(modifier.filepath))
            frames = list(pc2.iter_frames(path))
            error = 0.0
            for frame, expected in enumerate(frames, 1):
                bpy.context.scene.frame_set(frame)
                evaluated = cloth.evaluated_get(bpy.context.evaluated_depsgraph_get())
                mesh = evaluated.to_mesh()
                try:
                    for vertex, target in zip(mesh.vertices, expected):
                        error = max(error, *(abs(vertex.co[i] - float(target[i])) for i in range(3)))
                finally:
                    evaluated.to_mesh_clear()
            moved = float(abs(frames[-1][:, 2] - frames[0][:, 2]).max())
            pinned = list(cloth.get("pinned_indices", []))
            if pinned:
                pin_drift = float(max(abs(frames[k][i] - frames[0][i]).max()
                                      for k in range(len(frames)) for i in pinned))
                entry.setdefault("pin_drift", {})[cloth.name] = pin_drift
                ok = ok and pin_drift < 1e-3
            entry["pc2"][cloth.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                        "frames": len(frames), "playback_max_error": error,
                                        "max_vertical_motion": round(moved, 4)}
            ok = ok and len(frames) == args.frames and error < 1e-5 and moved > 0.01
        entry["solver_processes_after"] = solver_processes() - before
        ok = ok and entry["solver_processes_after"] == 0
        report["runs"].append(entry)
        print(json.dumps(entry), flush=True)
    if args.repeat > 1 and not args.cancel_after:
        hashes = [{name: item["sha256"] for name, item in run["pc2"].items()} for run in report["runs"]]
        report["bit_identical_runs"] = all(h == hashes[0] for h in hashes)
        if args.reproducible:
            ok = ok and report["bit_identical_runs"]
    report["result"] = "PASS" if ok else "FAIL"
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"result": report["result"]}), flush=True)
    addon_utils.disable(args.module)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
