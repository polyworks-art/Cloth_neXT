"""Replay the concrete cached NEON export through the production solver session.

Only native field strength differs. Reuses the historical captured animation,
geometry, materials and quality; no Blender evaluation or source modification.
"""
import argparse
import hashlib
import json
import sys
import copy
from dataclasses import asdict
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.ppf.schema import envelope
from cloth_next.ppf.coordinates import solver_world_matrix
from cloth_next.ppf.resolver import SolverResolver, SolverResolutionContext
from cloth_next.ppf.layout import BundledSolverLayout
from cloth_next.ppf_run.session import SessionScene, SessionDeformable, SolverSession, new_project_name
from cloth_next.gaia.container import GaiaContainer
from tools.run_ppf_vertical_slice import _version_probe


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--frames', type=int, default=30)
    p.add_argument('--frontend-direct', action='store_true')
    p.add_argument('--water', type=Path, default=Path('F:/16_Cloth_neXt/cache_AD/S04_NEON_Garments_002_WaterFlow.gaia'))
    p.add_argument('--labels', nargs='+', default=['dry1', 'dry2', 'wet1', 'wet5'])
    p.add_argument('--saved-zones', action='store_true')
    a = p.parse_args()
    a.output = a.output.resolve()
    a.output.mkdir(exist_ok=False, parents=True)
    cache = Path('F:/16_Cloth_neXt/cache_AD')
    scene_dir = cache / '.cloth_next_export/scene/00/006a0ef0ba8466634b590c5dd7bb9f2a7e7295e2ecc7438d129afa5beebf175c'
    plan = json.loads((scene_dir / 'metadata.json').read_text())['plan']
    base = envelope.loads_envelope((cache / '.cloth_next_export/param/c3/c3f3dacd959b78c10104af0d1c25c29bfecbb4b753d6345fa5e7c0bbd1cc928d/param.cbor').read_bytes(), envelope.KIND_PARAM, schema_version=2)
    base['scene']['frames'] = a.frames-1
    data = scene_dir / 'scene.cbor'
    exe = Path('C:/Users/timch/AppData/Local/ClothNeXt/solver/versions/official-2026-10-13-12-32-win64/target/cuda/release/ppf-cts-server.exe')
    layout = BundledSolverLayout.from_executable(exe)
    resolved = SolverResolver(_version_probe).resolve(SolverResolutionContext(managed_root=layout.root_directory))
    assert resolved.executable_path.resolve() == exe.resolve()
    targets = plan['targets']
    zone_bindings = []
    if a.saved_zones:
        saved = json.loads((ROOT / 'docs/validation/gaia026/coupling-investigation/saved-settings.json').read_text())
        objects = {o['name']: o for part in envelope.loads_envelope(data.read_bytes(), envelope.KIND_SCENE, schema_version=2)
                   for o in part['object']}
        for obj in saved:
            if obj.get('face_material_params'):
                source = objects[obj['name']]
                geometry = np.load(ROOT / ('docs/validation/gaia026/coupling-investigation/' + obj['name'] + '-mesh.npz'))
                np.testing.assert_array_equal(source['face'], geometry['triangles'])
                zone_bindings.append(dict(uuid=source['uuid'], faces=source['face'], params=obj['face_material_params']))
    deformables = tuple(SessionDeformable(t['name'], base['group'][i][2][0],
        t['initial_shape'][0], 'CLOTH', solver_world_matrix(t['world_matrix'])) for i,t in enumerate(targets))
    first = deformables[0]
    water = a.water.resolve()
    with GaiaContainer(water) as c:
        fingerprint = c.manifest['water']['fingerprint']
    descriptor = dict(version=1, field_kind='native-file', first=1, last=a.frames,
        fps=30., solver_fps=30., targets=[dict(path=str(water), fingerprint=fingerprint,
            groups=list(range(3)), influence=1., strength=1., flow=1.)])
    report = dict(scope='Historical NEON scene replay, identical captured animation and material/quality; field is sole A/B variable', water=str(water), cases=[])
    for label,strength in [('dry1',0),('dry2',0),('wet1',1),('wet2',1),('wet5',5),
                           ('softdry1',0),('softdry2',0),('softwet1',1),('softwet2',1),
                           ('nocontactdry',0),('nocontactwet',1)]:
        if label not in a.labels:
            continue
        folder = a.output / label
        folder.mkdir()
        case_tree = copy.deepcopy(base)
        if label.startswith('soft'):
            for group in case_tree['group'][:3]:
                group[0].update({'density':.08, 'young-mod':200., 'poiss-rat':.35,
                    'bend':.1, 'deformation-damping':.0005, 'bending-damping':.0005})
        if label.startswith('nocontact'):
            case_tree['scene']['disable-contact'] = True
        payload = envelope.dumps_envelope(envelope.KIND_PARAM, case_tree, schema_version=2)
        descriptor['targets'][0]['strength'] = strength
        scene = SessionScene(new_project_name(), first.name, first.uuid, first.vertex_count,
            base['group'][3][1][0], base['group'][3][2][0], a.frames, data, payload,
            hashlib.sha256(data.read_bytes()).hexdigest(), hashlib.sha256(payload).hexdigest(),
            deformables=deformables, water_stream_json=json.dumps(descriptor) if strength else '')
        if a.frontend_direct:
            from frontend import BlenderApp, App, Utils
            from cloth_next.gaia.native_fluid import native_fluid_payload_file
            import shutil
            import time
            if Utils.busy():
                raise RuntimeError('Another native solver is active')
            App.set_backend('cuda')
            app = BlenderApp('cnx_neon_ab_' + label + '_' + new_project_name())
            project = Path(app._root)
            shutil.copyfile(data, project / 'data.pickle')
            if strength:
                native_fluid_payload_file(payload, descriptor, project / 'param.pickle')
            else:
                (project / 'param.pickle').write_bytes(payload)
            started = time.monotonic()
            print(label, 'BUILDING', project, flush=True)
            app.populate().make()
            fixed = app._fixed_session
            if zone_bindings and not label.startswith('soft'):
                from cloth_next.ppf.official_scene_worker import apply_material_tables
                mapping = envelope.loads_envelope((project / 'session/map.pickle').read_bytes(), envelope.KIND_VERTEX_MAP, schema_version=2)
                apply_material_tables(project / 'session/bin', mapping, zone_bindings)
            print(label, 'SIMULATING', flush=True)
            fixed.start(blocking=True)
            assert fixed.finished()
            poses = np.asarray([fixed.get.vertex(i)[0] for i in range(a.frames)])
            assert np.isfinite(poses).all()
            # Official map supplies the dynamic order; retain complete native
            # positions and map for explicit per-object checks downstream.
            np.save(folder / 'native-positions.npy', poses)
            shutil.copyfile(project / 'param.pickle', folder / 'param.cbor')
            report['cases'].append(dict(label=label, strength=strength,
                native_project=str(project), seconds=time.monotonic()-started,
                positions_shape=list(poses.shape), groups=case_tree['group'],
                disable_contact=case_tree['scene']['disable-contact'],
                saved_zones_applied=bool(zone_bindings and not label.startswith('soft'))))
            (a.output / 'report.json').write_text(json.dumps(report, indent=2))
            continue
        events = []
        frames = []
        def emit(e):
            events.append(asdict(e))
            if not events[:-1] or events[-2]['phase'] != e.phase:
                print(label, e.phase, e.message, flush=True)
        session = SolverSession(resolved=resolved, scene=scene, work_directory=folder,
            emit=emit, frame_sink=frames.append)
        diagnostics = session.run()
        frames.sort(key=lambda f:f.solver_frame)
        assert len(frames) == a.frames-1
        for obj in deformables:
            positions = np.asarray([f.positions_by_uuid[obj.uuid] for f in frames])
            assert np.isfinite(positions).all()
            np.save(folder / (obj.name + '.npy'), positions)
        (folder / 'events.json').write_text(json.dumps(events, indent=2, default=str))
        (folder / 'diagnostics.json').write_text(json.dumps(asdict(diagnostics), indent=2, default=str))
        report['cases'].append(dict(label=label, strength=strength,
            contact_peak=diagnostics.contact_peak, timings=diagnostics.timings))
        (a.output / 'report.json').write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
