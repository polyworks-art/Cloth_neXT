# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Isolated real-solver matrix for Cloth NeXt per-face friction.

This diagnostic deliberately uses the production scene/parameter encoders and
session implementation without importing Blender.  It does not modify solver
inputs or add fallback behaviour; it records whether each exact input succeeds.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cloth_next.ppf.schema import envelope
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.schema.data import SceneObject, encode_scene
from cloth_next.ppf_run import fixture
from tools.run_ppf_vertical_slice import run


def _cases(triangle_count: int) -> dict[str, tuple[float, ...]]:
    half = triangle_count // 2
    return {
        "global": (),
        "constant_025": (0.25,) * triangle_count,
        "two_regions": (0.05,) * half + (0.95,) * (triangle_count - half),
        "alternating": tuple(
            0.05 if index % 2 == 0 else 0.95
            for index in range(triangle_count)
        ),
        "strong_variation": tuple(
            (index % 11) / 10.0 for index in range(triangle_count)
        ),
        "global_identical_050": (0.5,) * triangle_count,
    }


def _validate_payload(values: tuple[float, ...], divisions: int) -> dict:
    cloth, collider = fixture.vertical_slice_fixture()
    if divisions != fixture.CLOTH_DIVISIONS:
        vertices, triangles = fixture.cloth_grid(divisions=divisions)
        cloth = fixture.FixtureMesh(
            fixture.CLOTH_NAME, vertices, triangles,
            (0.0, 0.0, fixture.CLOTH_HEIGHT))
    scene_cloth = SceneObject(
        cloth.name, "diagnostic-cloth", cloth.vertices_local, cloth.triangles,
        solver_world_matrix(cloth.world_matrix), face_friction=values,
    )
    scene_collider = SceneObject(
        collider.name, "diagnostic-collider", collider.vertices_local,
        collider.triangles, solver_world_matrix(collider.world_matrix),
    )
    blob, digest = encode_scene(scene_cloth, scene_collider)
    decoded = envelope.loads_envelope(blob, envelope.KIND_SCENE)
    shell = decoded[0]["object"][0]
    decoded_values = tuple(shell.get("face_friction", ()))
    if values:
        assert len(values) == len(cloth.triangles)
        assert decoded_values == tuple(
            scene_cloth.info_dict()["face_friction"])
    else:
        assert "face_friction" not in shell
    assert all(math.isfinite(value) and 0.0 <= value <= 1.0
               for value in values)
    for triangle in cloth.triangles:
        assert len(set(triangle)) == 3
        assert all(0 <= index < len(cloth.vertices_local)
                   for index in triangle)
        a, b, c = (cloth.vertices_local[index] for index in triangle)
        ab = tuple(b[i] - a[i] for i in range(3))
        ac = tuple(c[i] - a[i] for i in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        assert sum(component * component for component in cross) > 0.0
    return {
        "scene_hash_validation": digest,
        "vertex_count": len(cloth.vertices_local),
        "triangle_count": len(cloth.triangles),
        "face_friction_count": len(values),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
        "decoded_face_friction_count": len(decoded_values),
        "payload_structure": [
            {"type": group["type"],
             "object_keys": sorted(group["object"][0].keys())}
            for group in decoded
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--divisions", type=int,
                        default=fixture.CLOTH_DIVISIONS)
    parser.add_argument("--case", action="append", dest="cases")
    args = parser.parse_args()

    vertices, triangles = fixture.cloth_grid(divisions=args.divisions)
    cloth = fixture.FixtureMesh(
        fixture.CLOTH_NAME, vertices, triangles,
        (0.0, 0.0, fixture.CLOTH_HEIGHT))
    available = _cases(len(cloth.triangles))
    selected = args.cases or list(available)
    unknown = sorted(set(selected) - set(available))
    if unknown:
        parser.error(f"unknown cases: {unknown}; choices: {sorted(available)}")

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    matrix = []
    for name in selected:
        values = available[name]
        record = {
            "case": name, "cloth_divisions": args.divisions,
            **_validate_payload(values, args.divisions),
        }
        try:
            report = run(
                args.solver.resolve(), args.output_dir / name,
                frame_count=args.frames, face_friction=values,
                cloth_divisions=args.divisions,
            )
            record.update({
                "result": "PASS",
                "scene_hash": report["data_hash"],
                "param_hash": report["param_hash"],
                "last_successful_frame": max(
                    report["solver_frames_fetched"], default=0),
                "contact_peak": report.get("contact_peak"),
                "solver_exit_code": 0,
                "wall_time_s": report["wall_time_s"],
                "pc2_path": report["pc2_path"],
            })
        except BaseException as exc:  # diagnostic must preserve every failure
            context = getattr(getattr(exc, "record", None), "context", ())
            context_dict = dict(context) if context else {}
            record.update({
                "result": "FAIL",
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "traceback": traceback.format_exc(),
                "solver_exit_code": context_dict.get("exit_code"),
            })
        matrix.append(record)
        (args.output_dir / "matrix.json").write_text(
            json.dumps(matrix, indent=2), encoding="utf-8")
        print(json.dumps({key: record.get(key) for key in (
            "case", "result", "last_successful_frame", "contact_peak",
            "solver_exit_code", "wall_time_s", "exception")}, indent=2),
            flush=True)
    return 1 if any(row["result"] != "PASS" for row in matrix) else 0


if __name__ == "__main__":
    raise SystemExit(main())
