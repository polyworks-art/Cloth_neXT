# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with official PPF Python: build native tables through its public API."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(directory, name):
    import cbor2
    import numpy as np
    from frontend import App, Session
    from cloth_next.ppf.project_links import ProjectLink
    from cloth_next.ppf.official_scene_worker import apply_material_tables

    directory = Path(directory).resolve()
    project = directory / name
    canonical = Path(App.get_data_dirpath()).resolve() / name
    link = ProjectLink(canonical, project, owned_root=directory, project_name=name)
    link.ensure()
    try:
        project.joinpath('.cash').mkdir(exist_ok=True)
        # Public native authoring API avoids this installation's server/junction
        # directory creation issue. The CNX production binder is unchanged.
        app = App.create(name, cache_dir=str(project / '.cash'))
        data = cbor2.loads(directory.joinpath('data.pickle').read_bytes())['payload'][0]['object'][0]
        app.asset.add.tri('zone-boundary', np.asarray(data['vert']), np.asarray(data['face']))
        scene = app.scene.create()
        obj = scene.add('zone-boundary')
        params = cbor2.loads(directory.joinpath('param.pickle').read_bytes())['payload']['group'][0][0]
        for key, value in params.items():
            obj.param.set(key, value)
        fixed = scene.build()
        native = project / 'native-input'
        fixed.export_fixed(str(native), delete_exist=False)
        document = json.loads(directory.joinpath('bindings.json').read_text())
        binary = native / 'bin'
        mapping = cbor2.loads((binary.parent / 'map.pickle').read_bytes())['payload']['zone-boundary']
        apply_material_tables(binary, {'zone-boundary': mapping}, document['face_material_params'])
        # The official public triangle table also retains the exact values in
        # a saved FixedSession. Build it at the owned, resolved filesystem path.
        for key in document['face_material_params'][0]['params']:
            fixed.tri_param[key] = np.fromfile(binary / 'param' / f'tri-{key}.bin', dtype=np.float32).tolist()
        saved = Session(name, str(project), str(Path.cwd()), str(canonical.parent), 'session').init(fixed).build()
        binary = Path(saved.info.path) / 'bin'
        actual_tri = np.fromfile(binary / 'tri.bin', dtype=np.uint64).reshape(-1, 3)
        lookup = {tuple(sorted(tri)): i for i, tri in enumerate(actual_tri)}
        faces = ((0, 1, 2), (1, 3, 2))
        ordinals = [lookup[tuple(sorted(mapping[i] for i in face))] for face in faces]
        report = {'built_native_session': True, 'tables': {}}
        for key, expected in document['face_material_params'][0]['params'].items():
            values = np.fromfile(binary / 'param' / f'tri-{key}.bin', dtype=np.float32)[ordinals]
            np.testing.assert_array_equal(values, np.asarray(expected, dtype=np.float32))
            np.testing.assert_array_equal(np.asarray(saved.fixed_scene.tri_param[key], dtype=np.float32)[ordinals],
                                          np.asarray(expected, dtype=np.float32))
            report['tables'][key] = values.tolist()
        directory.joinpath('material-zones-report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
    finally:
        link.remove()


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
