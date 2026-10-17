"""Collect actual operator, native-output and PC2 evidence without another solve."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.bake.pc2 import iter_frames
from cloth_next.ppf.schema import envelope


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    wet=a.root/'test-a-131-133-r9';dry=a.root/'dry-131-133'
    wr=json.loads((wet/'report.json').read_text());wv=json.loads((wet/'verification.json').read_text())
    dr=json.loads((dry/'report.json').read_text());dv=json.loads((dry/'verification.json').read_text())
    assert wr['state']==dr['state']=='FINISHED' and wv['result']==dv['result']=='PASS'
    scene_path=next((wet/'cache/.cloth_next_export/scene').rglob('scene.cbor'))
    scene=envelope.loads_envelope(scene_path.read_bytes(),envelope.KIND_SCENE,schema_version=2)
    objects={o['name']:o for part in scene for o in part['object']}
    comparisons=[]
    for record in wv['playback']:
        other=next(r for r in dv['playback'] if r['object']==record['object'])
        wx=np.asarray(list(iter_frames(Path(record['pc2']))),float)
        dx=np.asarray(list(iter_frames(Path(other['pc2']))),float)
        linear=np.asarray(objects[record['object']]['transform'])[:3,:3]
        distances=np.linalg.norm((wx-dx)@linear.T,axis=2)
        assert float(distances[0].max())<1e-7
        comparisons.append(dict(object=record['object'],rms_mm=float(np.sqrt((distances**2).mean())*1000),
            max_mm=float(distances.max()*1000),last_rms_mm=float(np.sqrt((distances[-1]**2).mean())*1000),
            last_max_mm=float(distances[-1].max()*1000)))
    native=Path(wv['native_output']);mapping=envelope.loads_envelope((native.parent/'map.pickle').read_bytes(),
        envelope.KIND_VERTEX_MAP,schema_version=2)
    positions=np.stack([np.fromfile(native/f'vert_{i}.bin',dtype='<f4').reshape(-1,3) for i in (1,2)])
    audit=a.output/'collider-audit';audit.mkdir(exist_ok=True)
    np.save(audit/'native-positions.npy',positions)
    shutil.copy2(native.parent/'map.pickle',audit/'map.cbor')
    (audit/'report.json').write_text(json.dumps(dict(source_scene=str(scene_path.resolve()),
        cases=[dict(label='hydro-131-133',artifact_root=str(audit.resolve()),session=str(native.parent))]),indent=2))
    requests=list((wet/'session-evidence').glob('hydrodynamic-*/request.json'))
    request=json.loads(requests[0].read_text());b=request['bounds']
    lo=np.array([b[0],b[2],-b[4]]);hi=np.array([b[3],b[5],-b[1]])
    cloth=np.concatenate([mapping[o['uuid']] for name,o in objects.items() if name.startswith('S04_NEON_Garments')])
    cv=positions[:,cloth]
    bounds=dict(output_vertices_inside=bool(((cv>=lo)&(cv<=hi)).all()),
        minimum_clearance_m=float(np.minimum(cv-lo,hi-cv).min()),
        scope='Output cloth vertices; native device guard additionally checks force quadrature at every iterate')
    original=Path('F:/16_Cloth_neXt/SW04/SW04_NEON.blend')
    def digest(path):
        with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
    source_integrity=dict(original_blend_sha256=digest(original),test_copy_sha256=digest(a.root/'SW04_NEON-copy.blend'))
    source_integrity['original_matches_untouched_test_copy']=source_integrity['original_blend_sha256']==source_integrity['test_copy_sha256']
    caches=[]
    for playback in wv['playback']:
        meta=Path(playback['pc2']).with_suffix('.meta.json')
        data=json.loads(meta.read_text());path=meta.parent/data['cache_file']
        caches.append(dict(path=str(path),unchanged=digest(path)==data['cache_sha256']))
    assert len(caches)==3 and all(r['unchanged'] for r in caches)
    gui=json.loads((wet/'gui-status.json').read_text()) if (wet/'gui-status.json').is_file() else None
    result=dict(test_a='PASS',test_a_frames=[131,133],fluid_frames=[131,134],
        test_b='BLOCKED_SOURCE_SURFACE',test_b_requested_frames=[130,159],
        blocker='Source 130 has 7 boundary edges and 1 non-manifold edge; source 135 also non-manifold. Disconnected closed components are accepted (378 at frame 131).',
        blender=wr['blender'],addon_version=wr['addon_version'],attachment_count=wr['attachment_count'],
        binary_identity=json.loads((a.root/'profile/hydrodynamics.json').read_text()),
        hydro_operator_seconds=wr['seconds'],dry_operator_seconds=dr['seconds'],
        wet_dry=comparisons,sdf=bounds,source_integrity=source_integrity,
        native_vs_pc2_max_m=max(r['native_max_error_m'] for r in wv['playback']),
        reload_playback_max_local_units=max(r['reload_playback_max_error_m'] for r in wv['playback']),
        forces=wv['hydrodynamics'],gui_cancel=gui,caches_after_cancel=caches,
        reference_limit='The previous frames 1-30 references have a different fluid/animation clock and are not a matching reference for frames 131-133.',
        known_release_blockers=['30-frame mid-sequence source safety','strict CPU/CUDA self-contact force parity 19/20','local repeatability spread remains documented'])
    (a.output/'summary.json').write_text(json.dumps(result,indent=2))
    for path,label in ((wet/'report.json','hydro-operator.json'),(wet/'verification.json','hydro-verification.json'),
                       (dry/'report.json','dry-operator.json'),(dry/'verification.json','dry-verification.json'),
                       (a.root/'native-bounds-check-r2/report.json','native-bounds.json')):
        shutil.copy2(path,a.output/label)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
