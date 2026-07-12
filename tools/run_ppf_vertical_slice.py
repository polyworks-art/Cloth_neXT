# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Standalone real-solver vertical slice: no Blender, no mocks.

Generates the same deterministic cloth/collider fixture the Blender test
scene uses, starts the pinned real PPF solver, uploads the encoded scene,
builds, simulates 8 Blender frames (7 solver frames), incrementally fetches
and validates the output, writes a PC2 playback cache plus a JSON report,
stops the owned solver, and exits non-zero on any failure.

Usage:
    python tools/run_ppf_vertical_slice.py \
        --solver "C:/path/to/ppf-cts-server.exe" \
        --output-dir "<temporary-directory>"

``--solver`` falls back to the ``CLOTH_NEXT_PPF_EXECUTABLE`` environment
variable. Uses the production encoder, transport, and session service — the
exact code the Blender operator runs.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

assert "bpy" not in sys.modules, "the standalone harness must not import bpy"

from cloth_next.ppf.coordinates import solver_world_matrix, transform_point
from cloth_next.ppf.resolver import (SolverResolutionContext, SolverResolver,
                                     development_executable_from_environment)
from cloth_next.ppf.schema.data import SceneObject, encode_scene
from cloth_next.ppf.schema.params import SimulationSettings, encode_param
from cloth_next.ppf_run import fixture, import_result
from cloth_next.ppf_run.session import (SessionScene, SolverFrame,
                                       SolverSession, new_project_name)


def _version_probe(executable: Path) -> tuple[str, str, str]:
    from cloth_next.ppf.models import ConnectionOwnership
    from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager
    config = SolverProcessConfig(
        executable_path=executable, working_directory=executable.parent,
        connect_timeout=10.0, ownership_mode=ConnectionOwnership.OWNED_PROCESS)
    return SolverProcessManager(config).executable_version()


def run(solver_executable: Path, output_dir: Path, fps: int = 24) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    cloth, collider = fixture.vertical_slice_fixture()
    frame_count = fixture.FRAME_END - fixture.FRAME_START + 1

    resolver = SolverResolver(_version_probe)
    resolved = resolver.resolve(SolverResolutionContext(
        development_executable=solver_executable))
    if resolved is None or resolved.executable_path is None:
        raise SystemExit(f"no solver executable at {solver_executable}")

    cloth_uuid = f"clothnext-cloth-{new_project_name()[10:]}"
    collider_uuid = f"clothnext-collider-{new_project_name()[10:]}"
    scene_cloth = SceneObject(cloth.name, cloth_uuid, cloth.vertices_local,
                              cloth.triangles,
                              solver_world_matrix(cloth.world_matrix))
    scene_collider = SceneObject(collider.name, collider_uuid,
                                 collider.vertices_local, collider.triangles,
                                 solver_world_matrix(collider.world_matrix))
    data_payload, data_hash = encode_scene(scene_cloth, scene_collider)
    settings = SimulationSettings(frame_count=frame_count, fps=fps,
                                  gravity_blender=fixture.DEFAULT_GRAVITY)
    param_payload, param_hash = encode_param(
        settings, cloth.name, cloth_uuid, collider.name, collider_uuid)

    scene = SessionScene(
        project_name=new_project_name(),
        cloth_name=cloth.name, cloth_uuid=cloth_uuid,
        cloth_vertex_count=len(cloth.vertices_local),
        collider_name=collider.name, collider_uuid=collider_uuid,
        frame_count=frame_count,
        data_payload=data_payload, param_payload=param_payload,
        data_hash=data_hash, param_hash=param_hash)

    frames: list[SolverFrame] = []
    events: list[str] = []

    def emit(event) -> None:
        line = f"[{event.phase}] {event.message}"
        events.append(line)
        print(line, flush=True)

    session = SolverSession(resolved=resolved, scene=scene,
                            work_directory=output_dir,
                            emit=emit, frame_sink=frames.append)
    started = time.time()
    diagnostics = session.run()
    duration = time.time() - started

    # -- validation ----------------------------------------------------------
    solver_frame_count = frame_count - 1
    assert sorted(f.solver_frame for f in frames) == \
        list(range(1, solver_frame_count + 1)), "frame set incomplete"
    initial_world_solver = [
        transform_point(scene_cloth.transform, v) for v in cloth.vertices_local]
    last = max(frames, key=lambda f: f.solver_frame)
    assert all(len(f.positions_solver_world) == len(cloth.vertices_local)
               for f in frames), "vertex count changed"
    max_displacement = max(
        math.dist(a, b) for a, b in
        zip(initial_world_solver, last.positions_solver_world))
    assert max_displacement > 0.01, (
        f"cloth did not move under gravity (max displacement "
        f"{max_displacement:.6f} m)")
    assert all(math.isfinite(c) for f in frames
               for p in f.positions_solver_world for c in p), "non-finite output"

    playback = import_result.build_playback_frames(
        cloth.vertices_local, frames, cloth.world_matrix,
        expected_frame_count=frame_count)
    pc2_path = output_dir / "cn_test_cloth.pc2"
    header = import_result.write_playback_cache(pc2_path, playback)

    report = {
        "result": "PASS",
        "run_id": diagnostics.run_id,
        "project_name": diagnostics.project_name,
        "solver_executable": str(resolved.executable_path),
        "solver_mode": diagnostics.solver_mode,
        "package_version": diagnostics.package_version,
        "protocol_version": diagnostics.protocol_version,
        "schema_version": diagnostics.schema_version,
        "host": diagnostics.host,
        "port": diagnostics.port,
        "process_id": diagnostics.process_id,
        "upload_id": diagnostics.upload_id,
        "data_hash": data_hash,
        "param_hash": param_hash,
        "data_payload_bytes": len(data_payload),
        "param_payload_bytes": len(param_payload),
        "cloth_vertices": len(cloth.vertices_local),
        "cloth_triangles": len(cloth.triangles),
        "collider_vertices": len(collider.vertices_local),
        "collider_triangles": len(collider.triangles),
        "blender_frames": frame_count,
        "solver_frames_fetched": diagnostics.fetched_frames,
        "status_transitions": diagnostics.status_transitions,
        "max_cloth_displacement_m": max_displacement,
        "pc2_path": str(pc2_path),
        "pc2_header": {"vertex_count": header.vertex_count,
                       "frame_count": header.frame_count,
                       "start_frame": header.start_frame,
                       "sample_rate": header.sample_rate},
        "timings_s": diagnostics.timings,
        "wall_time_s": duration,
    }
    (output_dir / "vertical_slice_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", type=Path,
                        default=development_executable_from_environment(),
                        help="path to ppf-cts-server.exe "
                             "(default: CLOTH_NEXT_PPF_EXECUTABLE)")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=24)
    args = parser.parse_args()
    if args.solver is None:
        parser.error("--solver or CLOTH_NEXT_PPF_EXECUTABLE is required")
    try:
        report = run(args.solver.resolve(), args.output_dir.resolve(),
                     fps=args.fps)
    except BaseException as exc:  # noqa: BLE001 — non-zero exit on ANY failure
        print(f"VERTICAL SLICE FAILED: {exc}", file=sys.stderr)
        raise
    print(json.dumps(report, indent=2))
    print("VERTICAL SLICE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
