"""Compare complete native runs without presenting them as Blender bake validation."""
import argparse
import itertools
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--scene-cache', type=Path, required=True)
    parser.add_argument('--dependencies', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(args.dependencies), str(Path(__file__).resolve().parents[1])]
    import numpy as np
    import trimesh
    from cloth_next.ppf.schema import envelope

    manifest = json.loads((args.suite/'manifest.json').read_text())
    assert len(manifest['runs']) >= 5, 'Five complete runs required for this report'
    assert all(r['exit_code'] == 0 and r['vertex_outputs'] == 30 and r['finite'] for r in manifest['runs'])
    mapping = envelope.loads_envelope((args.fixture/'map.pickle').read_bytes(), envelope.KIND_VERTEX_MAP, schema_version=2)
    objects = {}
    for path in sorted(args.scene_cache.rglob('scene.cbor')):
        for part in envelope.loads_envelope(path.read_bytes(), envelope.KIND_SCENE, schema_version=2):
            for obj in part['object']:
                if obj['uuid'] in mapping:
                    objects[obj['name']] = np.asarray(mapping[obj['uuid']], dtype=int)
    assert sum(name.startswith('S04_NEON_Garments') for name in objects) == 3
    triangles = np.fromfile(args.fixture/'bin/tri.bin', dtype='<u8').reshape(-1, 3).astype(int)
    runs = []
    for record in manifest['runs']:
        folder = args.suite/str(record['run'])/'output'
        x = np.stack([np.fromfile(folder/f'vert_{k}.bin', dtype='<f4').reshape(-1, 3) for k in range(30)])
        hydro = [np.fromfile(folder/f'hydro_{k}.bin', dtype='<f4').reshape(-1, 16) for k in range(1, 30)]
        assert np.isfinite(x).all() and all(np.isfinite(h).all() for h in hydro)
        for h in hydro:
            assert (h[:, :4] >= 0).all() and (h[:, 1] <= h[:, 0]+1e-7).all()
            assert (h[:, 3] == 0).all(), 'Frozen Ct=0 must remain active'
            bound = .5*1000*h[:, 1].astype(float)*h[:, 14].astype(float)**2
            assert (h[:, 2]-bound <= 1e-5+1e-4*bound).all(), 'Existing pressure bound failed'
        runs.append((record['run'], x, hydro))
    result = dict(scope='Native world-space trajectory repeatability; PC2, Blender playback and independent collision audits remain separate gates.',
                  investigation_threshold_m=.008, resources=manifest['runs'], pairs=[])
    for (left, x, hx), (right, y, hy) in itertools.combinations(runs, 2):
        pair = dict(left=left, right=right, objects=[], fluid=[])
        for name, indices in objects.items():
            delta = np.linalg.norm(y[:, indices].astype(float)-x[:, indices], axis=2)
            frame, vertex = np.unravel_index(delta.argmax(), delta.shape)
            lookup = np.full(x.shape[1], -1)
            lookup[indices] = np.arange(len(indices))
            faces = lookup[triangles]
            face_ids = np.flatnonzero((faces >= 0).all(axis=1))
            faces = faces[face_ids]
            geometry = dict(method='All vertices and triangle connectivity exactly identical', maximum_m=0.)
            if delta.max() != 0:
                geometry = dict(method='Bidirectional sampled point-to-triangle distances at worst material-vertex frame; not Hausdorff', directions=[])
                meshes = [trimesh.Trimesh(z[frame, indices], faces, process=False) for z in (x, y)]
                for source, target in (meshes, meshes[::-1]):
                    points = np.concatenate((source.vertices, source.triangles.mean(axis=1)))
                    points = points[np.linspace(0, len(points)-1, min(4096, len(points)), dtype=int)]
                    _, distance, _ = trimesh.proximity.closest_point(target, points)
                    geometry['directions'].append(dict(samples=len(points), maximum_m=float(distance.max()), rms_m=float(np.sqrt(np.mean(distance**2)))))
            frames = [dict(frame=130+k, rms_m=float(np.sqrt(np.mean(d*d))), maximum_m=float(d.max()), above_8mm=int((d > .008).sum())) for k, d in enumerate(delta)]
            pair['objects'].append(dict(object=name, rms_m=float(np.sqrt(np.mean(delta**2))), maximum_m=float(delta.max()),
                                        worst_frame=130+int(frame), worst_material_vertex=int(vertex), frames=frames, geometry=geometry))
            if name.startswith('S04_NEON_Garments'):
                for k, (a, b) in enumerate(zip(hx, hy), 1):
                    ids = face_ids[face_ids < len(a)]
                    pair['fluid'].append(dict(object=name, frame=130+k,
                        force_total_delta_n=float(abs(a[ids, 2].astype(float).sum()-b[ids, 2].astype(float).sum())),
                        wet_area_total_delta_m2=float(abs(a[ids, 1].astype(float).sum()-b[ids, 1].astype(float).sum())),
                        maximum_force_triangle_delta_n=float(np.max(abs(a[ids, 2].astype(float)-b[ids, 2]), initial=0)),
                        maximum_wet_triangle_delta_m2=float(np.max(abs(a[ids, 1].astype(float)-b[ids, 1]), initial=0)),
                        telemetry_bit_identical=bool(np.array_equal(a[ids].view('u4'), b[ids].view('u4')))))
        result['pairs'].append(pair)
    result['maximum_material_vertex_deviation_m'] = max(o['maximum_m'] for p in result['pairs'] for o in p['objects'])
    result['all_trajectory_bits_identical'] = all(np.array_equal(runs[0][1].view('u4'), run[1].view('u4')) for run in runs[1:])
    result['screen'] = 'INVESTIGATE' if result['maximum_material_vertex_deviation_m'] > .008 else 'WITHIN_EXISTING_SCREEN; NOT PRODUCTION CERTIFICATION'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k not in ('pairs', 'resources')}, indent=2))


if __name__ == '__main__':
    main()
