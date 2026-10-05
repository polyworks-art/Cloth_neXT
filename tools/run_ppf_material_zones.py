# SPDX-License-Identifier: GPL-3.0-or-later
"""Build/run a real external PPF session and inspect exact triangle input tables."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import os
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from cloth_next.materials import DEFAULT_SHELL_SETTINGS, DEFAULT_STATIC_SETTINGS
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.schema.data import GROUP_SHELL, SceneObject, encode_multi_deformable_scene, internal_static_sentinel
from cloth_next.ppf.schema.params import SimulationSettings, encode_multi_deformable_param
from cloth_next.ppf_run.session import SessionDeformable, SessionScene, SolverSession, new_project_name
from tools.run_ppf_vertical_slice import _version_probe


def run(executable, output_dir):
    output_dir = Path(output_dir).resolve()
    resolved = SolverResolver(_version_probe).resolve(SolverResolutionContext(development_executable=Path(executable)))
    matrix = solver_world_matrix(((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)))
    vertices = ((0, 0, 2), (1, 0, 2), (0, 1, 2), (1, 1, 2))
    faces = ((0, 1, 2), (1, 3, 2))
    # Exercise every advertised parameter in the real frontend/solver.
    tables = {'bend': (10, 100), 'young-mod': (1000, 2000),
              'friction': (.1, .5), 'deformation-damping': (.01, .02),
              'bending-damping': (.03, .04)}
    obj = SceneObject('Zone boundary', 'zone-boundary', vertices, faces, matrix,
                      face_material_params=tables)
    static = internal_static_sentinel()
    material = replace(DEFAULT_SHELL_SETTINGS, bend_resistance=10, stretch_resistance=1000)
    schema, protocol = int(resolved.schema_version), resolved.protocol_version
    data, dh = encode_multi_deformable_scene(((obj, GROUP_SHELL),), (static,), schema_version=schema)
    params, ph = encode_multi_deformable_param(
        SimulationSettings(frame_count=2, fps=30, gravity_blender=(0, 0, 0)),
        ((obj.name, obj.uuid, GROUP_SHELL, material, None),),
        ((static.name, static.uuid, DEFAULT_STATIC_SETTINGS),),
        contact_enabled=False, schema_version=schema, protocol_version=protocol)
    scene = SessionScene(new_project_name(), obj.name, obj.uuid, 4, '', '', 2,
                         data, params, dh, ph,
                         deformables=(SessionDeformable(obj.name, obj.uuid, 4),))
    from cloth_next.ppf.official_scene_bridge import prepare_for_solver, uses_official_bridge
    if uses_official_bridge(resolved):
        from cloth_next.updater.health_runner import bundle_root_for
        prepared = prepare_for_solver(scene, resolved)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / 'data.pickle').write_bytes(prepared.data_payload)
        (output_dir / 'param.pickle').write_bytes(prepared.param_payload)
        (output_dir / 'bindings.json').write_text(prepared.official_bridge_json)
        root = bundle_root_for(Path(executable))
        interpreter = root / 'python' / ('python.exe' if os.name == 'nt' else 'bin/python3')
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(root)
        environment['PPF_CTS_DATA_ROOT'] = ''
        result = subprocess.run([str(interpreter), str(Path(__file__).with_name('ppf_material_zones_worker.py')),
                                 str(output_dir), scene.project_name], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=120)
        (output_dir / 'frontend.log').write_text(result.stdout + '\n' + result.stderr)
        if result.returncode:
            raise RuntimeError(result.stdout + '\n' + result.stderr)
        return json.loads((output_dir / 'material-zones-report.json').read_text())
    frames = []
    report = {'frames': 0, 'protocol': protocol, 'tables': {}}
    class InspectSession(SolverSession):
        def _apply_official_bindings(self):
            super()._apply_official_bindings()
            inspect_tables()
    def inspect_tables():
        binary = output_dir / 'server-data' / scene.project_name / 'session' / 'bin'
        from cloth_next.ppf.schema import cbor_codec
        mapping = cbor_codec.loads((binary.parent / 'map.pickle').read_bytes())['payload'][obj.uuid]
        actual_tri = np.fromfile(binary / 'tri.bin', dtype=np.uint64).reshape(-1, 3)
        lookup = {tuple(sorted(tri)): i for i, tri in enumerate(actual_tri)}
        ordinals = [lookup[tuple(sorted(mapping[i] for i in face))] for face in faces]
        for key, expected in tables.items():
            values = np.fromfile(binary / 'param' / f'tri-{key}.bin', dtype=np.float32)
            actual = values[ordinals]
            np.testing.assert_array_equal(actual, np.asarray(expected, dtype=np.float32))
            report['tables'][key] = actual.tolist()
    session = InspectSession(resolved=resolved, scene=scene, work_directory=output_dir, frame_sink=frames.append)
    session.run()
    report['frames'] = len(frames)
    assert report['tables']['bend'] == [10.0, 100.0]
    Path(output_dir, 'material-zones-report.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solver', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.solver, args.output_dir), indent=2))
