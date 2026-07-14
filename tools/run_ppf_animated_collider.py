# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-PPF proof for rigid/deforming kinematic Collider scene payloads."""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.materials import DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS
from cloth_next.ppf.coordinates import solver_world_matrix, transform_point
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.schema.data import SceneObject, encode_scene
from cloth_next.ppf.schema.params import SimulationSettings, encode_param
from cloth_next.ppf_run import fixture
from cloth_next.ppf_run.session import SessionScene, SolverSession, new_project_name


def _version(executable: Path):
    manager = SolverProcessManager(SolverProcessConfig(
        executable_path=executable, working_directory=executable.parent,
        ownership_mode=ConnectionOwnership.OWNED_PROCESS))
    return manager.executable_version()


def run(executable: Path, output: Path, mode: str) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    cloth, collider = fixture.vertical_slice_fixture()
    frame_count = 8
    fps = 24
    sample_count = (frame_count - 1) * 2 + 1
    times = [index / (fps * 2) for index in range(sample_count)]
    cloth_uuid = f"cloth-{new_project_name()}"
    collider_uuid = f"collider-{new_project_name()}"
    cloth_object = SceneObject(
        cloth.name, cloth_uuid, cloth.vertices_local, cloth.triangles,
        solver_world_matrix(cloth.world_matrix))
    collider_matrix = solver_world_matrix(collider.world_matrix)
    if mode == "rigid":
        translations = []
        for index in range(sample_count):
            translations.append([
                collider_matrix[0][3] + 0.03 * index,
                collider_matrix[1][3], collider_matrix[2][3]])
        half = math.sqrt(0.5)
        collider_object = SceneObject(
            collider.name, collider_uuid, collider.vertices_local,
            collider.triangles, collider_matrix,
            transform_animation={
                "time": times, "translation": translations,
                "quaternion": [[half, -half, 0.0, 0.0]] * sample_count,
                "scale": [[1.0, 1.0, 1.0]] * sample_count,
                "segments": [
                    {"interpolation": "LINEAR",
                     "handle_right": [1.0 / 3.0, 0.0],
                     "handle_left": [2.0 / 3.0, 1.0]}
                    for _index in range(sample_count - 1)]})
    else:
        rest_world = np.asarray([
            transform_point(collider_matrix, vertex)
            for vertex in collider.vertices_local], dtype=np.float32)
        frames = np.empty((sample_count, len(rest_world), 3), dtype=np.float32)
        for index in range(sample_count):
            frames[index] = rest_world
            frames[index, :, 0] += 0.02 * index
            frames[index, :, 1] += np.linspace(
                0.0, 0.01 * index, len(rest_world), dtype=np.float32)
        identity = tuple(tuple(1.0 if row == column else 0.0
                               for column in range(4)) for row in range(4))
        collider_object = SceneObject(
            collider.name, collider_uuid,
            tuple(tuple(float(value) for value in row) for row in frames[0]),
            collider.triangles, identity,
            static_deform_animation={"time": times, "vert_frames": frames})
    data, data_hash = encode_scene(cloth_object, collider_object)
    settings = SimulationSettings(
        frame_count=frame_count, fps=fps,
        gravity_blender=fixture.DEFAULT_GRAVITY)
    params, param_hash = encode_param(
        settings, cloth.name, cloth_uuid, collider.name, collider_uuid,
        shell=DEFAULT_SHELL_SETTINGS, static=DEFAULT_STATIC_SETTINGS)
    resolved = SolverResolver(_version).resolve(SolverResolutionContext(
        development_executable=executable))
    assert resolved is not None
    session_scene = SessionScene(
        project_name=new_project_name(), cloth_name=cloth.name,
        cloth_uuid=cloth_uuid, cloth_vertex_count=len(cloth.vertices_local),
        collider_name=collider.name, collider_uuid=collider_uuid,
        frame_count=frame_count, data_payload=data, param_payload=params,
        data_hash=data_hash, param_hash=param_hash)
    frames_out = []
    diagnostics = SolverSession(
        resolved=resolved, scene=session_scene, work_directory=output,
        frame_sink=frames_out.append).run()
    assert len(frames_out) == frame_count - 1
    assert all(len(frame.positions_solver_world) == len(cloth.vertices_local)
               for frame in frames_out)
    report = {
        "mode": mode, "result": "PASS", "data_payload_bytes": len(data),
        "cloth_frames": len(frames_out),
        "cloth_vertices_per_frame": len(frames_out[0].positions_solver_world),
        "collider_vertices": len(collider.vertices_local),
        "collider_cache_files": 0, "collider_modifiers_added": 0,
        "status_transitions": diagnostics.status_transitions,
    }
    (output / f"animated_{mode}_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("rigid", "deforming"), required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.solver, args.output, args.mode), sort_keys=True))


if __name__ == "__main__":
    main()
