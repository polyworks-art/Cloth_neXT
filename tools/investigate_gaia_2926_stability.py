"""Read-only native repeatability and force diagnostics; never changes physics."""
import argparse
import hashlib
import itertools
import json
import re
import sys
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--run', action='append', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--dependencies', type=Path, required=True)
a = p.parse_args()
sys.path[:0] = [str(a.dependencies), str(Path(__file__).resolve().parents[1])]
import numpy as np
import trimesh
from cloth_next.core.filesystem_paths import io_path
from cloth_next.ppf.schema import envelope


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def load(run):
    report = json.loads((run/'report.json').read_text())
    assert report['result'] == 'PASS' and report['range'] == [130, 159]
    native = io_path(Path(report['native_output']), reserved_length=1000)
    scene = next((run/'cache/.cloth_next_export/scene').rglob('scene.cbor'))
    parts = envelope.loads_envelope(scene.read_bytes(), envelope.KIND_SCENE, schema_version=2)
    objects = {o['name']: o for part in parts for o in part['object']}
    mapping = envelope.loads_envelope((native.parent/'map.pickle').read_bytes(), envelope.KIND_VERTEX_MAP, schema_version=2)
    ids = {name: np.asarray(mapping[o['uuid']], int) for name, o in objects.items() if o['uuid'] in mapping}
    x = np.stack([np.fromfile(native/f'vert_{k}.bin', dtype='<f4').reshape(-1, 3) for k in range(30)]).astype(float)
    tri = np.fromfile(native.parent/'bin/tri.bin', dtype='<u8').reshape(-1, 3).astype(int)
    owner = np.full(x.shape[1], '', dtype=object)
    for name, indices in ids.items():
        owner[indices] = name
    faces = {}
    for name, indices in ids.items():
        mask = (owner[tri] == name).all(1)
        lookup = np.full(x.shape[1], -1); lookup[indices] = np.arange(len(indices))
        faces[name] = (lookup[tri[mask]], np.flatnonzero(mask))
    telemetry = {k: np.fromfile(native/f'hydro_{k}.bin', dtype='<f4').reshape(-1, 16).astype(float) for k in range(1, 30)}
    text = (native.parent/'stdout.log').read_text(errors='replace')
    initial_trace = {key: [float(v) for v in re.findall(r'^\* '+re.escape(key)+r'\s*[:=]\s*([+\-\deE.]+)', text, re.M)[:5]] for key in ('time', 'reresid', 'max_dx', 'final_dt', 'num_contact')}
    advances = []
    for block in text.split('====== advance ======')[1:]:
        def values(key):
            return [float(v) for v in re.findall(r'^\* '+re.escape(key)+r'\s*[:=]\s*([+\-\deE.]+)', block, re.M)]
        if not values('time'):
            continue
        advances.append({key: values(key) for key in ('time', 'final_dt', 'newton_steps', 'iter', 'reresid', 'num_contact', 'max_dx')})
    fingerprints = {'native/'+str(f.relative_to(native.parent/'bin')): digest(f) for f in (native.parent/'bin').rglob('*.bin')}
    for volume in (run/'session-evidence').glob('hydrodynamic-*/volumes'):
        for file in volume.rglob('*.bin'):
            fingerprints['fluid/'+str(file.relative_to(volume))] = digest(file)
    return dict(label=run.name, run=run, native=native, report=report, x=x, ids=ids, faces=faces, tri=tri, owner=owner, telemetry=telemetry, advances=advances, fingerprints=fingerprints, parameter_sha256=digest(native.parent/'param.toml'), model_sha256=[digest(f) for f in (run/'session-evidence').glob('hydrodynamic-*/model.json')], initial_solver_trace=initial_trace)


def convergence(r, k):
    blocks = [b for b in r['advances'] if (k-1)/30 <= b['time'][0] < k/30]
    def all_values(key):
        return [v for b in blocks for v in b[key]]
    return dict(advance_steps=len(blocks), newton_steps=sum(all_values('newton_steps')), linear_iterations=sum(all_values('iter')), maximum_linear_iterations=max(all_values('iter'), default=None), maximum_relative_residual=max(all_values('reresid'), default=None), minimum_final_dt_s=min(all_values('final_dt'), default=None), maximum_contacts=max(all_values('num_contact'), default=None))


def attachment_positions(r, k):
    """Cross-stitch source vertices and barycentric targets, not collider pins."""
    params = envelope.loads_envelope(next((r['run']/'cache/.cloth_next_export/param').rglob('param.cbor')).read_bytes(), envelope.KIND_PARAM, schema_version=2)
    mapping = envelope.loads_envelope((r['native'].parent/'map.pickle').read_bytes(), envelope.KIND_VERTEX_MAP, schema_version=2)
    anchors = []; source_ids = []
    for group in params.get('cross_stitch', []):
        source = np.asarray(mapping[group['source_uuid']], int)
        target = np.asarray(mapping[group['target_uuid']], int)
        for indices, weights in zip(group['ind'], group['w']):
            index = int(source[indices[0]]); source_ids.append(index)
            anchors.append(r['x'][k, index]); anchors.append(np.asarray(weights[3:])@r['x'][k, target[indices[3:]]])
    return np.asarray(anchors), source_ids


def frame_metrics(r, k):
    v = r['telemetry'][k]; wet = v[:, 1].sum()
    assert np.isfinite(v).all() and (v[:, :4] >= 0).all() and (v[:, 1] <= v[:, 0]+1e-7).all() and (v[:, 3] == 0).all()
    bound = .5*1000*v[:, 1]*v[:, 14]**2
    assert (v[:, 2]-bound <= 1e-5+1e-4*bound).all()
    clock = json.loads((r['native']/f'hydro_{k}.io.json').read_text())
    by_object = {}
    for name, (_, face_ids) in r['faces'].items():
        face_ids = face_ids[face_ids < len(v)]
        if name.startswith('S04_NEON_Garments'):
            t = v[face_ids]; w = t[:, 1].sum()
            normals = []
            for sample in (k-1, k):
                points = r['x'][sample, r['tri'][face_ids]]
                n = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
                normals.append(n/np.maximum(np.linalg.norm(n, axis=1)[:, None], 1e-30))
            angles = np.degrees(np.arccos(np.clip((normals[0]*normals[1]).sum(1), -1, 1)))
            speed = np.linalg.norm((r['x'][k, r['ids'][name]]-r['x'][k-1, r['ids'][name]])*30, axis=1)
            by_object[name] = dict(force_n=float(t[:, 2].sum()), wet_m2=float(w), mean_fluid_speed_m_s=float(t[:, 7].sum()/max(w, 1e-30)), mean_relative_speed_m_s=float(t[:, 8].sum()/max(w, 1e-30)), cloth_rms_speed_m_s=float(np.sqrt(np.mean(speed**2))), wet_weighted_normal_rotation_deg=float(angles@t[:, 1]/max(w, 1e-30)), normal_rotation_p95_deg=float(np.quantile(angles, .95)))
    colliders = np.concatenate([ids for name, ids in r['ids'].items() if not name.startswith('S04_NEON_Garments')])
    motion = np.linalg.norm((r['x'][k, colliders]-r['x'][k-1, colliders])*30, axis=1)
    return dict(output=k, scene_frame=130+k, evaluation_source_frame=130+30*clock['evaluation_time_s'], dt_s=clock['dt_s'], force_n=float(v[:, 2].sum()), wet_m2=float(wet), mean_fluid_speed_m_s=float(v[:, 7].sum()/max(wet, 1e-30)), mean_relative_speed_m_s=float(v[:, 8].sum()/max(wet, 1e-30)), wet_sensitivity_10um_m2=float((v[:, 9]+v[:, 10]-2*v[:, 11]).sum()), collider_rms_speed_m_s=float(np.sqrt(np.mean(motion**2))), collider_max_speed_m_s=float(motion.max()), objects=by_object, convergence=convergence(r, k))


def compare(left, right):
    records = []
    for name in left['report']['playback']:
        name = name['object']; x = left['x'][:, left['ids'][name]]; y = right['x'][:, right['ids'][name]]
        assert x.shape == y.shape and np.array_equal(x[0], y[0])
        assert np.array_equal(left['faces'][name][0], right['faces'][name][0])
        d = np.linalg.norm(y-x, axis=2); k, vertex = np.unravel_index(d.argmax(), d.shape)
        frames = [dict(frame=130+i, rms_m=float(np.sqrt(np.mean(v*v))), maximum_m=float(v.max()), above_8mm=int((v > .008).sum()), vertex_indices_above_8mm=np.flatnonzero(v > .008).tolist()) for i, v in enumerate(d)]
        faces = left['faces'][name][0]; mx = trimesh.Trimesh(x[k], faces, process=False); my = trimesh.Trimesh(y[k], faces, process=False)
        distances = []
        for source, target in ((mx, my), (my, mx)):
            points = np.concatenate([source.vertices, source.triangles.mean(1)])
            _, dist, _ = trimesh.proximity.closest_point(target, points)
            distances.append(dict(samples=len(points), rms_m=float(np.sqrt(np.mean(dist**2))), maximum_m=float(dist.max()), p99_m=float(np.quantile(dist, .99))))
        delta = y[k, vertex]-x[k, vertex]; normal = mx.vertex_normals[vertex]; normal_delta = abs(float(delta@normal))
        affected = np.flatnonzero(d[k] > .008); local = [vertex] if not len(affected) else affected.tolist()
        # Nearest collider surface is a proximity measurement, not a collision verdict.
        other_meshes = [trimesh.Trimesh(left['x'][k, ids], left['faces'][n][0], process=False) for n, ids in left['ids'].items() if not n.startswith('S04_NEON_Garments')]
        collider = trimesh.util.concatenate(other_meshes)
        _, cd, _ = trimesh.proximity.closest_point(collider, x[k, local])
        _, worst_cd, _ = trimesh.proximity.closest_point(collider, x[k, vertex:vertex+1])
        incident = np.any(faces == vertex, axis=1); ring = np.unique(faces[incident]); nonlocal_faces = faces[~np.isin(faces, ring).any(1)]
        _, self_distance, _ = trimesh.proximity.closest_point(trimesh.Trimesh(x[k], nonlocal_faces, process=False), x[k, vertex:vertex+1])
        pin_ids = []
        for file in (left['native'].parent/'bin').glob('pin-ind-*.bin'):
            pin_ids.extend(np.fromfile(file, dtype='<u8').tolist())
        pin_ids = np.unique(pin_ids).astype(int); attachment_distance = float(np.linalg.norm(left['x'][k, pin_ids]-x[k, vertex], axis=1).min()) if len(pin_ids) else None
        global_faces = left['faces'][name][1][incident]; t = left['telemetry'][max(1, k)][global_faces]
        fold = float(np.min(mx.face_normals[incident]@normal))
        records.append(dict(object=name, rms_m=float(np.sqrt(np.mean(d*d))), maximum_m=float(d.max()), worst_frame=130+int(k), worst_vertex=int(vertex), world_solver_m=x[k, vertex].tolist(), world_blender_m=(x[k, vertex][[0, 2, 1]]*[1, -1, 1]).tolist(), normal_component_m=normal_delta, tangential_component_m=float(np.sqrt(max(0, float(delta@delta)-normal_delta**2))), frames=frames, sampled_surface_at_worst_frame=distances, worst_frame_affected_bounds_m=[x[k, local].min(0).tolist(), x[k, local].max(0).tolist()], affected_collider_distance_range_m=[float(cd.min()), float(cd.max())], worst_vertex_collider_distance_m=float(worst_cd[0]), worst_vertex_nonlocal_self_surface_distance_m=float(self_distance[0]), worst_vertex_nearest_pin_distance_m=attachment_distance, minimum_incident_normal_dot=fold, incident_wet_fraction=(t[:, 1]/np.maximum(t[:, 0], 1e-30)).tolist(), incident_wet_sensitivity_m2=float((t[:, 9]+t[:, 10]-2*t[:, 11]).sum())))
        anchors, attachment_sources = attachment_positions(left, k)
        record = records[-1]
        record['worst_vertex_nearest_kinematic_pin_distance_m'] = record.pop('worst_vertex_nearest_pin_distance_m')
        record['worst_vertex_nearest_attachment_distance_m'] = float(np.linalg.norm(anchors-x[k, vertex], axis=1).min())
        record['worst_vertex_is_attachment_source'] = int(left['ids'][name][vertex]) in attachment_sources
    changed = [key for key in set(left['fingerprints']) | set(right['fingerprints']) if left['fingerprints'].get(key) != right['fingerprints'].get(key)]
    forces = [abs(left['telemetry'][k][:, 2].sum()-right['telemetry'][k][:, 2].sum()) for k in range(1, 30)]
    wet = [abs(left['telemetry'][k][:, 1].sum()-right['telemetry'][k][:, 1].sum()) for k in range(1, 30)]
    force_frames = []
    for k in range(1, 30):
        clocks = [json.loads((r['native']/f'hydro_{k}.io.json').read_text()) for r in (left, right)]
        by_object = {}
        for name in (o['object'] for o in records):
            values = [r['telemetry'][k][r['faces'][name][1]] for r in (left, right)]
            by_object[name] = dict(force_delta_n=float(values[1][:, 2].sum()-values[0][:, 2].sum()), wet_delta_m2=float(values[1][:, 1].sum()-values[0][:, 1].sum()))
        force_frames.append(dict(output=k, final_evaluation_time_delta_s=clocks[1]['evaluation_time_s']-clocks[0]['evaluation_time_s'], force_delta_n=float(right['telemetry'][k][:, 2].sum()-left['telemetry'][k][:, 2].sum()), wet_delta_m2=float(right['telemetry'][k][:, 1].sum()-left['telemetry'][k][:, 1].sum()), objects=by_object))
    return dict(left=left['label'], right=right['label'], changed_binary_inputs=changed, identical_native_parameters=left['parameter_sha256'] == right['parameter_sha256'], identical_hydrodynamic_model=left['model_sha256'] == right['model_sha256'], maximum_force_delta_n=float(max(forces)), maximum_wet_delta_m2=float(max(wet)), force_frames=force_frames, objects=records)


a.output.mkdir(parents=True, exist_ok=True)
runs = [load(run.resolve()) for run in a.run]
result = dict(metric='World-space corresponding material vertices in metres; sampled surface distances separately reported, not Hausdorff.', contact_scope='Proximity at worst output frame only; no contact-pair causal proof. Telemetry is last native evaluation, not an output-time recomputation.', runs={r['label']: dict(initial_solver_trace=r['initial_solver_trace'], parameter_sha256=r['parameter_sha256'], input_binary_count=len(r['fingerprints']), frames=[frame_metrics(r, k) for k in range(1, 30)]) for r in runs}, pairs=[])
for left, right in itertools.combinations(runs, 2):
    item = compare(left, right); result['pairs'].append(item)
    (a.output/'analysis.json').write_text(json.dumps(result, indent=2))
    print(left['label'], right['label'], [(o['object'], o['rms_m'], o['maximum_m'], o['worst_frame']) for o in item['objects']], flush=True)
