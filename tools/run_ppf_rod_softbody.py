# SPDX-License-Identifier: GPL-3.0-or-later
"""Real PPF smoke tests for Cloth NeXt ROD and SOLID encoders."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cloth_next.materials import DEFAULT_STATIC_SETTINGS
from cloth_next.materials.deformables import RodMaterialSettings, SoftBodyMaterialSettings
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.schema.data import SceneObject, encode_deformable_scene
from cloth_next.ppf.schema.params import SimulationSettings, encode_deformable_param
from cloth_next.ppf_run.session import SessionScene, SolverSession, new_project_name

IDENTITY = solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0),
                                (0, 0, 1, 0), (0, 0, 0, 1)))


def _probe(executable: Path):
    from cloth_next.ppf.models import ConnectionOwnership
    from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager
    return SolverProcessManager(SolverProcessConfig(
        executable_path=executable, working_directory=executable.parent,
        ownership_mode=ConnectionOwnership.OWNED_PROCESS)).executable_version()


def _run(resolved, output: Path, kind: str):
    uid = f"smoke-{kind.lower()}"
    if kind == "ROD":
        vertices = ((-0.5, 0, 1), (0, 0, 1), (0.5, 0, 1))
        deformable = SceneObject("Rod", uid, vertices, (), IDENTITY,
                                 edges=((0, 1), (1, 2)))
        material = RodMaterialSettings()
    else:
        vertices = ((-0.2, -0.2, 0.8), (0.2, -0.2, 0.8),
                    (0.0, 0.2, 0.8), (0.0, 0.0, 1.2))
        deformable = SceneObject("Soft", uid, vertices,
            ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)), IDENTITY)
        material = SoftBodyMaterialSettings()
    collider = SceneObject("Floor", "smoke-floor",
        ((-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)),
        ((0, 1, 2), (0, 2, 3)), IDENTITY)
    data, data_hash = encode_deformable_scene(
        deformable, collider, group_type=kind)
    settings = SimulationSettings(3, 24, (0, 0, -9.81))
    params, param_hash = encode_deformable_param(
        settings, deformable.name, uid,
        ((collider.name, collider.uuid, DEFAULT_STATIC_SETTINGS),),
        group_type=kind, material=material)
    scene = SessionScene(new_project_name(), deformable.name, uid, len(vertices),
        collider.name, collider.uuid, 3, data, params, data_hash, param_hash,
        deformable_type=kind, deformable_world_matrix=IDENTITY)
    frames = []
    session = SolverSession(resolved=resolved, scene=scene,
        work_directory=output / kind.lower(), frame_sink=frames.append,
        simulate_timeout=300, build_timeout=300)
    # Preserve the generated project on a failed smoke run so command/stdout
    # logs remain inspectable. Successful runs still use a unique temp root.
    session._delete_project = lambda: None
    try:
        session.run()
    except Exception:
        print(json.dumps({"kind": kind,
            "stdout": session.diagnostics.stdout_tail,
            "stderr": session.diagnostics.stderr_tail,
            "states": session.diagnostics.status_transitions}, indent=2),
            flush=True)
        raise
    if len(frames) != 2 or any(not math.isfinite(v) for frame in frames
                               for point in frame.positions_solver_world for v in point):
        raise RuntimeError(f"{kind} returned invalid frames")
    return {"frames": len(frames), "vertices": len(frames[-1].positions_solver_world)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    resolved = SolverResolver(_probe).resolve(SolverResolutionContext(
        development_executable=args.solver))
    if resolved is None:
        raise SystemExit("solver could not be resolved")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {kind: _run(resolved, output, kind)
              for kind in ("ROD", "SOLID")}
    print(json.dumps({"result": "PASS", **report}, indent=2))


if __name__ == "__main__":
    main()
