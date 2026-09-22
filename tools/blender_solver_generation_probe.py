# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Real headless Blender Bake pipeline with a chosen verified solver binary."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.blender import registration, solver_test  # noqa: E402
from cloth_next.ppf_run.session import SolverSession  # noqa: E402
from cloth_next.updater.solver_registry import load_registry, write_registry  # noqa: E402


def main() -> None:
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("cuda", "cpu", "rocm", "lumen"))
    parser.add_argument("--choice", choices=("AUTO", "CUDA", "ROCM", "CPU"))
    parser.add_argument("--frames", type=int, default=35)
    parser.add_argument("--dt", type=float, default=0.01)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--installation-id", required=True)
    args = parser.parse_args(values)
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["CLOTH_NEXT_PPF_EXECUTABLE"] = str(args.solver.resolve())
    solver_root = (args.solver.resolve().parents[3]
                   if args.backend != "lumen" else args.solver.resolve().parents[2])
    os.environ["PATH"] = os.pathsep.join(
        (str(solver_root / "bin"), str(solver_root / "python"),
         os.environ.get("PATH", "")))
    write_registry(args.registry,
                   load_registry(args.registry).select(args.installation_id))
    solver_test.load_registry = lambda _path: load_registry(args.registry)
    solver_test._cancel_event.clear()

    registration.register()
    original_status = SolverSession._status
    try:
        choice = args.choice or (
            "CUDA" if args.backend == "lumen" else args.backend.upper())
        # Source-tree registration has no installed AddonPreferences entry.
        # Supply its selected value while retaining the normal resolver/Bake.
        solver_test._backend_choice = lambda _context: choice
        observed_status = []
        def capture_status(self, *args, **kwargs):
            response = original_status(self, *args, **kwargs)
            if "solver_backend" in response or "solver_target_dir" in response:
                observed_status.append({
                    "solver_backend": response.get("solver_backend"),
                    "solver_target_dir": response.get("solver_target_dir")})
            return response
        SolverSession._status = capture_status
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, -0.75))
        collider = bpy.context.object
        collider.name = "Certification Collider"
        collider.scale = (2.0, 2.0, 0.25)
        collider.cloth_next.enabled = True
        collider.cloth_next.role = "COLLIDER"
        bpy.ops.mesh.primitive_grid_add(x_subdivisions=7, y_subdivisions=7,
                                        size=2.0, location=(0.0, 0.0, 1.0))
        cloth = bpy.context.object
        cloth.name = "Generation Probe Cloth"
        cloth.cloth_next.enabled = True
        cloth.cloth_next.role = "CLOTH"
        cloth.cloth_next.bake_start = 1
        cloth.cloth_next.bake_end = args.frames
        cloth.cloth_next.cache_directory = str(args.output)
        scene = bpy.context.scene
        scene.cloth_next_quality.time_step = args.dt
        scene.render.fps = 24
        scene.frame_start = 1
        scene.frame_end = args.frames
        scene.gravity = (0.0, 0.0, -9.81)
        scene.frame_set(1)

        snapshot = solver_test.validate_scene(bpy.context)
        plan = solver_test.build_run_plan(bpy.context, snapshot=snapshot)
        print(f"CERT_PLAN frames={plan.scene.solver_frame_count} "
              f"exe={plan.resolved.executable_path}", flush=True)
        assert plan.resolved.installation_id == args.installation_id
        assert plan.resolved.executable_path.resolve() == args.solver.resolve()
        while not solver_test._queue.empty():
            solver_test._queue.get_nowait()
        solver_test._run_started_at = time.monotonic()
        gpu_samples = set()
        monitor_stop = threading.Event()
        def monitor_gpu():
            while not monitor_stop.wait(0.4):
                try:
                    sample = subprocess.run(
                        ["nvidia-smi", "--query-compute-apps=pid,process_name",
                         "--format=csv,noheader"], capture_output=True,
                        text=True, timeout=4, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    continue
                for line in sample.stdout.splitlines():
                    if "ppf-contact-solver.exe" in line.lower():
                        gpu_samples.add(line.strip())
        monitor = threading.Thread(target=monitor_gpu, daemon=True)
        monitor.start()
        try:
            solver_test._worker_main(plan)
        finally:
            monitor_stop.set()
            monitor.join(timeout=5)
        messages = []
        while not solver_test._queue.empty():
            messages.append(solver_test._queue.get_nowait())
        terminal = messages[-1]
        if terminal[0] != "finished":
            raise RuntimeError(str(terminal))
        headers, diagnostics = terminal[1], terminal[2]
        assert headers and diagnostics.protocol_version == plan.resolved.protocol_version
        assert list(diagnostics.fetched_frames) == list(range(1, args.frames))
        assert plan.pc2_path.is_file()
        solver_test._attach_playback(plan, headers)
        assert any(mod.type == "MESH_CACHE" and
                   Path(bpy.path.abspath(mod.filepath)).resolve() == plan.pc2_path.resolve()
                   for mod in cloth.modifiers)
        assert diagnostics.control_server_alive is False
        if args.backend in ("cuda", "cpu", "rocm"):
            expected_target = args.solver.resolve().parents[1]
            assert observed_status, "Gaia status did not expose backend evidence"
            assert all(item["solver_backend"] == args.backend and
                       Path(item["solver_target_dir"]).resolve() == expected_target
                       for item in observed_status), observed_status
        if args.backend in ("cuda", "lumen"):
            assert any(str(args.solver.resolve().parent).lower() in sample.lower()
                       for sample in gpu_samples), gpu_samples
        scene.frame_set(args.frames - 1)
        evaluated = cloth.evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert len(evaluated.to_mesh().vertices) == len(cloth.data.vertices)
        evaluated.to_mesh_clear()
        report = {
            "protocol": diagnostics.protocol_version,
            "schema": diagnostics.schema_version,
            "fetched_frames": diagnostics.fetched_frames,
            "cache_files": [str(plan.pc2_path)],
            "control_server_alive_after": diagnostics.control_server_alive,
            "executable": str(plan.resolved.executable_path),
            "installation_id": plan.resolved.installation_id,
            "process_id": diagnostics.process_id,
            "launch_id": diagnostics.solver_launch_id,
            "backend_status": observed_status[-1] if observed_status else None,
            "frame_end": args.frames,
            "playback_frame": scene.frame_current,
            "gpu_process_samples": sorted(gpu_samples),
        }
        print("CLOTH_NEXT_GENERATION_PROBE=" + json.dumps(report, sort_keys=True))
    finally:
        SolverSession._status = original_status
        registration.unregister()


if __name__ == "__main__":
    main()
