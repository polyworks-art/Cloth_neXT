"""Measure a completed regular NEON bake and stage its independent collision audit."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.ppf.schema import envelope
from cloth_next.core.filesystem_paths import io_path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--reference', type=Path)
    a = p.parse_args()
    a.run, a.output = a.run.resolve(), a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=True)
    operator = json.loads((a.run/'report.json').read_text())
    verified = json.loads((a.run/'verification.json').read_text())
    assert operator['state'] == 'FINISHED' and verified['result'] == 'PASS'
    first, last = operator['range']
    count = last-first+1
    native = io_path(Path(verified['native_output']), reserved_length=1000)
    scene_path = next((a.run/'cache/.cloth_next_export/scene').rglob('scene.cbor'))
    scene = envelope.loads_envelope(scene_path.read_bytes(), envelope.KIND_SCENE, schema_version=2)
    objects = {o['name']:o for part in scene for o in part['object']}
    mapping = envelope.loads_envelope((native.parent/'map.pickle').read_bytes(), envelope.KIND_VERTEX_MAP, schema_version=2)
    positions = np.stack([np.fromfile(native/f'vert_{i}.bin', dtype='<f4').reshape(-1,3) for i in range(count)])
    assert np.isfinite(positions).all()
    audit = a.output/'collider-audit'
    audit.mkdir(exist_ok=True)
    np.save(audit/'native-positions.npy', positions)
    shutil.copy2(native.parent/'map.pickle', audit/'map.cbor')
    (audit/'report.json').write_text(json.dumps(dict(source_scene=str(scene_path),
        cases=[dict(label=f'regular-blender-{first}-{last}', artifact_root=str(audit), session=str(native.parent))]), indent=2))
    request_path = next((a.run/'session-evidence').glob('hydrodynamic-*/request.json'))
    request = json.loads(request_path.read_text())
    b = request['bounds']
    lo, hi = np.array([b[0],b[2],-b[4]]), np.array([b[3],b[5],-b[1]])
    cloth_ids = np.concatenate([mapping[o['uuid']] for name,o in objects.items() if name.startswith('S04_NEON_Garments')])
    cv = positions[:,cloth_ids]
    inside = bool(((cv >= lo)&(cv <= hi)).all())
    assert inside, 'An output cloth vertex is outside the unchanged SDF bounds'
    tri = np.fromfile(native.parent/'bin/tri.bin', dtype='<u8').reshape(-1,3)
    owner = np.full(positions.shape[1], -1)
    names = [name for name in objects if name.startswith('S04_NEON_Garments')]
    for i,name in enumerate(names):
        owner[mapping[objects[name]['uuid']]] = i
    tri_owner = owner[tri]
    loads = []
    for path in sorted(native.glob('hydro_*.bin'), key=lambda p:int(p.stem.split('_')[1])):
        values = np.fromfile(path, dtype='<f4').reshape(-1,16)
        assert len(values) == len(tri) and np.isfinite(values).all()
        clock = json.loads(path.with_suffix('.io.json').read_text())
        per_object = {}
        for i,name in enumerate(names):
            v = values[(tri_owner == i).all(1)]
            wet = float(v[:,1].sum())
            per_object[name] = dict(area_m2=float(v[:,0].sum()), wet_area_m2=wet,
                normal_load_n=float(v[:,2].sum()), tangential_load_n=float(v[:,3].sum()),
                net_force_n=v[:,4:7].sum(0).tolist(), mean_wet_fluid_speed_m_s=float(v[:,7].sum()/max(wet,1e-30)),
                mean_wet_relative_speed_m_s=float(v[:,8].sum()/max(wet,1e-30)),
                maximum_fluid_speed_m_s=float(v[:,13].max()),
                maximum_relative_speed_m_s=float(v[:,14].max()),
                wet_classification_change_area_at_10um_m2=float((v[:,9]+v[:,10]-2*v[:,11]).sum()))
            assert float(v[:,3].sum()) == 0, 'Ct=0 must produce zero tangential load'
        loads.append(dict(output_frame=int(path.stem.split('_')[1]), evaluation_clock=clock, objects=per_object))
    sampled = json.loads((a.run/'resource-samples.json').read_text(encoding='utf-8-sig'))
    if isinstance(sampled, dict):sampled = [sampled]
    resources = dict(sample_count=len(sampled), interval_seconds=5,
        maximum_sampled_tree_rss_bytes=max(r['total_rss_bytes'] or 0 for r in sampled),
        maximum_sampled_tree_private_bytes=max(r['total_private_bytes'] or 0 for r in sampled),
        maximum_sampled_global_gpu_memory_mib=max(int(r['gpu_global_used_mib']) for r in sampled if r.get('gpu_global_used_mib')),
        scope='Sampled maxima from monitor start, not guaranteed process lifetime peaks; GPU memory is system-wide, not exclusive to this bake')
    result = dict(frames=[first,last], real_fluid_lookahead=last+1, operator_seconds=operator['seconds'],
        operator_state=operator['state'], verification=verified['result'], attachment_count=operator['attachment_count'],
        model=dict(Cn=1,Ct=0,coupling='one-way'), request=request, native_loads=loads,
        sdf=dict(all_output_cloth_vertices_inside=inside, minimum_output_clearance_m=float(np.minimum(cv-lo,hi-cv).min()),
                 native_iterate_guard='fail-closed-v1 remains active'), resources=resources,
        playback=verified['playback'], independent_collider_audit='PENDING',
        middle_sequence_130_159=('PENDING_PHYSICAL_VISUAL_ACCEPTANCE' if (first,last)==(130,159)
                                 else 'NOT_PASSED_BY_THIS_SEPARATE_TEST'))
    collision_path = audit/'collider-crossing-audit.json'
    if collision_path.exists():
        collision = json.loads(collision_path.read_text())
        assert len(collision['cases']) == count and collision['crossing_segments'] == 0
        result['independent_collider_audit'] = dict(result='PASS', frames=count,
            crossing_segments=0, scope=collision['scope'])
    if a.reference is not None:
        reference = json.loads((a.reference/'report.json').read_text())
        param_path = next((a.run/'cache/.cloth_next_export/param').rglob('param.cbor'))
        param = envelope.loads_envelope(param_path.read_bytes(), envelope.KIND_PARAM, schema_version=2)
        quality_diff = {k:dict(reference=v,regular=param['scene'].get(k)) for k,v in reference['quality'].items()
                        if param['scene'].get(k) != v}
        comparison = dict(quality_differences=quality_diff, cases=[],
            interpretation='Measured differences only; input differences and documented native repeatability must be considered. No new acceptance tolerance.')
        for label in ('hydro1','hydro2'):
            for name in names:
                old = np.load(a.reference/label/(name+'.npy'))
                current = positions[:,mapping[objects[name]['uuid']]]
                if current.shape != old.shape:
                    comparison['cases'].append(dict(case=label,object=name,error='incompatible trajectory shape'))
                    continue
                delta = np.linalg.norm(current.astype(float)-old.astype(float), axis=-1)
                comparison['cases'].append(dict(case=label,object=name,initial_max_m=float(delta[0].max()),
                    rms_m=float(np.sqrt(np.mean(delta**2))),maximum_m=float(delta.max()),last_rms_m=float(np.sqrt(np.mean(delta[-1]**2)))))
        result['native_reference_comparison'] = comparison
    (a.output/'summary.json').write_text(json.dumps(result, indent=2))
    for filename in ('report.json','verification.json','resource-samples.json'):
        shutil.copy2(a.run/filename, a.output/filename)
    print(json.dumps(dict(frames=result['frames'],native_force_samples=len(loads),sdf=result['sdf'],resources=resources), indent=2))


if __name__ == '__main__':
    main()
