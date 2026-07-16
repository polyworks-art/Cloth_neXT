# SPDX-License-Identifier: GPL-3.0-or-later
"""Standalone real-solver proof for two Cloth objects in one PPF project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.materials import DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS
from cloth_next.ppf.coordinates import solver_world_matrix, transform_point
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.schema.data import (GROUP_SHELL, SceneObject,
    encode_multi_deformable_scene,internal_static_sentinel)
from cloth_next.ppf.schema.params import (SimulationSettings,
    encode_multi_deformable_param)
from cloth_next.ppf_run import fixture
from cloth_next.ppf_run.session import (SessionDeformable, SessionScene,
    SolverSession, new_project_name)
from cloth_next.pinning import StaticPinConfig
from tools.run_ppf_vertical_slice import _version_probe


def run(executable: Path, output_dir: Path, *, include_collider: bool = True) -> dict:
    cloth, collider = fixture.vertical_slice_fixture()
    resolved = SolverResolver(_version_probe).resolve(
        SolverResolutionContext(development_executable=executable))
    if resolved is None:
        raise RuntimeError(f"solver could not be resolved: {executable}")
    identity = tuple(tuple(row) for row in cloth.world_matrix)
    shifted = tuple(tuple(row) for row in cloth.world_matrix)
    shifted = tuple(tuple(value + (1.2 if column == 3 and row == 0 else 0.0)
                          for column, value in enumerate(values))
                    for row, values in enumerate(shifted))
    uuid_a, uuid_b = "multi-cloth-a", "multi-cloth-b"
    collider_uuid = "multi-collider"
    solver_a = solver_world_matrix(identity)
    solver_b = solver_world_matrix(shifted)
    objects = (
        (SceneObject("ClothA", uuid_a, cloth.vertices_local, cloth.triangles,
                     solver_a, (0,)), GROUP_SHELL),
        (SceneObject("ClothB", uuid_b, cloth.vertices_local, cloth.triangles,
                     solver_b, (0,)), GROUP_SHELL))
    static = SceneObject("Floor", collider_uuid, collider.vertices_local,
        collider.triangles, solver_world_matrix(collider.world_matrix))
    statics = (static,) if include_collider else (internal_static_sentinel(),)
    data_payload, data_hash = encode_multi_deformable_scene(objects, statics)
    settings = SimulationSettings(
        4, 24, fixture.DEFAULT_GRAVITY, wind_blender=(0.5, 0.0, 0.0),
        air_density=0.001, air_friction=0.2, vertex_air_damp=0.01,
        dynamic_parameters=(
            ("wind", ((0.0, (0.5, 0.0, 0.0), False),
                      (2.0 / 24.0, (1.0, 0.0, 0.0), False))),
            ("air-density", ((0.0, (0.001,), False),
                             (2.0 / 24.0, (0.002,), False))),))
    times = tuple(frame / 24.0 for frame in range(4))
    start_a = transform_point(solver_a, cloth.vertices_local[0])
    start_b = transform_point(solver_b, cloth.vertices_local[0])
    pins_a = StaticPinConfig((0,), pin_group_id="multi-pins-a", times=times,
        positions=tuple(((start_a[0] + frame * 0.01,
                          start_a[1], start_a[2]),) for frame in range(4)))
    pins_b = StaticPinConfig((0,), pin_group_id="multi-pins-b", times=times,
        positions=tuple(((start_b[0], start_b[1] + frame * 0.01,
                          start_b[2]),) for frame in range(4)))
    deformables = (
        ("ClothA", uuid_a, GROUP_SHELL, DEFAULT_SHELL_SETTINGS, pins_a),
        ("ClothB", uuid_b, GROUP_SHELL, DEFAULT_SHELL_SETTINGS, pins_b))
    colliders = ((("Floor", collider_uuid, DEFAULT_STATIC_SETTINGS),)
                 if include_collider else ((statics[0].name,statics[0].uuid,
                                             DEFAULT_STATIC_SETTINGS),))
    param_payload, param_hash = encode_multi_deformable_param(
        settings, deformables, colliders)
    scene = SessionScene(
        new_project_name(), "ClothA", uuid_a, len(cloth.vertices_local),
        ("Floor" if include_collider else ""),
        (collider_uuid if include_collider else ""),
        4, data_payload, param_payload, data_hash,
        param_hash, deformables=(
            SessionDeformable("ClothA", uuid_a, len(cloth.vertices_local)),
            SessionDeformable("ClothB", uuid_b, len(cloth.vertices_local))))
    frames = []
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics = SolverSession(
        resolved=resolved, scene=scene, work_directory=output_dir,
        frame_sink=frames.append).run()
    assert [frame.solver_frame for frame in frames] == [1, 2, 3]
    assert all(set(frame.positions_by_uuid) == {uuid_a, uuid_b}
               for frame in frames)
    assert all(frame.positions_by_uuid[uuid_a].shape ==
               (len(cloth.vertices_local), 3) for frame in frames)
    report = {"result": "PASS", "frames": len(frames),
              "user_collider_count": int(include_collider),
              "internal_static_sentinel": not include_collider,
              "objects": sorted(frames[-1].positions_by_uuid),
              "vertices_per_object": len(cloth.vertices_local),
              "status_transitions": diagnostics.status_transitions}
    (output_dir / "multi_object_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-collider", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.solver.resolve(), args.output_dir.resolve(),
                         include_collider=not args.no_collider),
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
