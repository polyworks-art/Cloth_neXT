# SPDX-License-Identifier: GPL-3.0-or-later
"""Real-solver regression: sewing closes while both unpinned panels fall."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from cloth_next.attachments import AttachmentPoint, ObjectAttachment
from cloth_next.materials import DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS
from cloth_next.ppf.coordinates import solver_world_matrix, transform_point
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.schema.data import (
    GROUP_SHELL, SceneObject, encode_multi_deformable_scene, internal_static_sentinel)
from cloth_next.ppf.schema.params import SimulationSettings, encode_multi_deformable_param
from cloth_next.ppf_run.session import (
    SessionDeformable, SessionScene, SolverSession, new_project_name)
from cloth_next.sewing import solver_stitch_stiffness
from tools.run_ppf_vertical_slice import _version_probe


def run(executable: Path, output_dir: Path, *, mode="cross", strength=100.0,
        contact=True, frame_count=13):
    output_dir = output_dir.resolve()
    resolved = SolverResolver(_version_probe).resolve(
        SolverResolutionContext(development_executable=executable))
    if resolved is None:
        raise RuntimeError("Cannot resolve solver")
    matrix = solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0),
                                  (0, 0, 1, 0), (0, 0, 0, 1)))
    a = ((-.6, -.25, 2), (-.1, -.25, 2), (-.1, .25, 2), (-.6, .25, 2))
    b = ((.1, -.25, 2), (.6, -.25, 2), (.6, .25, 2), (.1, .25, 2))
    faces = ((0, 1, 2), (0, 2, 3))
    if mode == "cross":
        objects = (SceneObject("A", "sew-a", a, faces, matrix),
                   SceneObject("B", "sew-b", b, faces, matrix))
        target_uuid, triangle, targets = "sew-b", (0, 2, 3), (0, 3)
    else:
        objects = (SceneObject("A", "sew-a", a + b,
                   faces + tuple(tuple(i + 4 for i in tri) for tri in faces),
                   matrix, stitch_pairs=((1, 4), (2, 7)) if mode == "legacy" else ()),)
        target_uuid, triangle, targets = "sew-a", (4, 6, 7), (4, 7)
        if mode == "mixed":
            c = tuple((x + .7, y, z) for x, y, z in b)
            objects += (SceneObject("B", "sew-b", c, faces, matrix),)
    material = replace(DEFAULT_SHELL_SETTINGS, sewing_stiffness=strength,
                       shape_damping=0.0)
    attachments = () if mode == "legacy" else ((ObjectAttachment(
        "seam", "Seam", "sew-a", target_uuid, "CLOTH", "CLOTH",
        solver_stitch_stiffness(strength), tuple(AttachmentPoint(
            src, triangle, tuple(float(i == tgt) for i in triangle),
            a[src], b[j]) for j, (src, tgt) in enumerate(zip((1, 2), targets))),
        allow_same_object=True), len(objects[0].vertices_local),
        len(objects[0].vertices_local) if target_uuid == "sew-a"
        else len(objects[-1].vertices_local)),)
    if mode == "mixed":
        attachments += ((ObjectAttachment(
            "cross-seam", "Cross seam", "sew-a", "sew-b", "CLOTH", "CLOTH",
            solver_stitch_stiffness(strength), tuple(AttachmentPoint(
                src, (0, 2, 3), tuple(float(i == tgt) for i in (0, 2, 3)),
                objects[0].vertices_local[src], c[tgt])
                for src, tgt in ((5, 0), (6, 3)))), 8, 4),)
    static = internal_static_sentinel()
    schema, protocol = int(resolved.schema_version), resolved.protocol_version
    data, dh = encode_multi_deformable_scene(
        tuple((obj, GROUP_SHELL) for obj in objects), (static,), schema_version=schema)
    params, ph = encode_multi_deformable_param(
        SimulationSettings(frame_count, 24, (0, 0, -9.81), air_density=0.0,
                           air_friction=0.0, vertex_air_damp=0.0),
        tuple((obj.name, obj.uuid, GROUP_SHELL, material, None) for obj in objects),
        ((static.name, static.uuid, DEFAULT_STATIC_SETTINGS),),
        object_attachments=attachments, contact_enabled=contact,
        schema_version=schema, protocol_version=protocol)
    scene = SessionScene(new_project_name(), "A", "sew-a",
        len(objects[0].vertices_local), "", "", frame_count, data, params, dh, ph,
        deformables=tuple(SessionDeformable(obj.name, obj.uuid,
                                           len(obj.vertices_local)) for obj in objects))
    frames = []
    output_dir.mkdir(parents=True, exist_ok=True)
    SolverSession(resolved=resolved, scene=scene, work_directory=output_dir,
                  frame_sink=frames.append).run()
    final = frames[-1].positions_by_uuid
    initial = np.asarray([transform_point(matrix, v) for obj in objects
                          for v in obj.vertices_local])
    positions = np.concatenate([final[obj.uuid] for obj in objects])
    seam_a = final["sew-a"][[1, 2]]
    seam_b = final[target_uuid][list(targets)]
    report = {"mode": mode, "strength": strength, "contact": contact,
              "frames": len(frames),
              "seam_distance": np.linalg.norm(seam_a - seam_b, axis=1).tolist(),
              "center_drop": float(initial[:, 1].mean() - positions[:, 1].mean()),
              "seam_drop": float(2.0 - np.concatenate((seam_a, seam_b))[:, 1].mean())}
    report["first_frame_seam_distance"] = np.linalg.norm(
        frames[0].positions_by_uuid["sew-a"][[1, 2]]
        - frames[0].positions_by_uuid[target_uuid][list(targets)], axis=1).tolist()
    if mode == "mixed":
        report["cross_seam_distance"] = np.linalg.norm(
            final["sew-a"][[5, 6]] - final["sew-b"][[0, 3]], axis=1).tolist()
        report["cross_seam_drop"] = float(2.0 - np.concatenate((
            final["sew-a"][[5, 6]], final["sew-b"][[0, 3]]))[:, 1].mean())
        assert max(report["cross_seam_distance"]) < 0.003, report
        assert report["cross_seam_drop"] > 1.0, report
    assert np.isfinite(positions).all()
    if mode != "legacy" and strength == 100.0:
        assert max(report["seam_distance"]) < 0.003, report
        assert report["center_drop"] > 1.0, report
        assert report["seam_drop"] > 1.0, report
    (output_dir / "sewing_report.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("intra", "cross", "mixed", "legacy"), default="cross")
    parser.add_argument("--strength", type=float, default=100.0)
    parser.add_argument("--no-contact", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.solver, args.output_dir, mode=args.mode,
                         strength=args.strength, contact=not args.no_contact), indent=2))
