"""Report native sensitivity, including an explicit baseline repeatability floor."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from cloth_next.ppf.schema import envelope
r=json.loads((a.root/'report.json').read_text());assert len(r['cases'])==5 and all(x['finished'] and x['returncode']==0 for x in r['cases'])
original=Path('.hydro-test-tmp/blender-e2e-20261016/test-a-131-133-r9');scene=next((original/'cache/.cloth_next_export/scene').rglob('scene.cbor'));objects={o['name']:o for part in envelope.loads_envelope(scene.read_bytes(),envelope.KIND_SCENE,schema_version=2) for o in part['object']};baseline=Path(r['cases'][0]['session']);mapping=envelope.loads_envelope((baseline/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2)
tri=np.fromfile(baseline/'bin/tri.bin',dtype='<u8').reshape(-1,3);names=[n for n in objects if n.startswith('S04_NEON_Garments')];owner=np.full(max(max(ids) for ids in mapping.values())+1,-1)
for i,n in enumerate(names):owner[mapping[objects[n]['uuid']]]=i
poses={};loads={}
for case in r['cases']:
 s=Path(case['session']);poses[case['label']]=np.stack([np.fromfile(s/f'output/vert_{k}.bin',dtype='<f4').reshape(-1,3) for k in range(3)]).astype(float)
 loads[case['label']]=np.stack([np.fromfile(s/f'output/hydro_{k}.bin',dtype='<f4').reshape(-1,16) for k in (1,2)]).astype(float)
 assert np.isfinite(poses[case['label']]).all() and np.isfinite(loads[case['label']]).all()
original_native=Path(json.loads((original/'verification.json').read_text())['native_output'])
poses['reference_original']=np.stack([np.fromfile(original_native/f'vert_{k}.bin',dtype='<f4').reshape(-1,3) for k in range(3)]).astype(float)
loads['reference_original']=np.stack([np.fromfile(original_native/f'hydro_{k}.bin',dtype='<f4').reshape(-1,16) for k in (1,2)]).astype(float)
rows=[]
for case in [*r['cases'][1:],dict(label='reference_original')]:
 label=case['label']
 for i,n in enumerate(names):
  ids=mapping[objects[n]['uuid']];delta=np.linalg.norm(poses[label][:,ids]-poses['baseline1'][:,ids],axis=2);mask=(owner[tri]==i).all(1)
  b=loads['baseline1'][:,mask];c=loads[label][:,mask];wetb=b[:,:,1].sum(1);wetc=c[:,:,1].sum(1);fb=b[:,:,2].sum(1);fc=c[:,:,2].sum(1)
  wetdiff=abs(wetc-wetb);fdiff=abs(fc-fb);wetlimit=np.maximum(.005,.05*wetb);flimit=np.maximum(1.,.05*fb);rms=float(np.sqrt(np.mean(delta**2)));maximum=float(delta.max());drift=float(abs(np.diff(fc)-np.diff(fb)).max());driftlimit=.1*float(fb.mean())
  rows.append(dict(case=label,object=n,trajectory_rms_m=rms,trajectory_max_m=maximum,trajectory_screen='PASS' if rms<=.002 and maximum<=.008 else 'INVESTIGATE',baseline_wet_area_m2=wetb.tolist(),case_wet_area_m2=wetc.tolist(),wet_area_delta_m2=wetdiff.tolist(),wet_area_screen='PASS' if (wetdiff<=wetlimit).all() else 'INVESTIGATE',baseline_normal_load_n=fb.tolist(),case_normal_load_n=fc.tolist(),normal_load_delta_n=fdiff.tolist(),normal_load_screen='PASS' if (fdiff<=flimit).all() else 'INVESTIGATE',normal_load_first_difference_change_n=drift,temporal_screen='PASS' if drift<=max(1.,driftlimit) else 'INVESTIGATE',zero_tangential_load=bool((c[:,:,3]==0).all())))
result=dict(scope='Unchanged native CUDA on original 131–133 scene copies; 2 force outputs only. Temporal screen cannot certify 30-frame stability or visual quality.',criteria='docs/GAIA_PRACTICAL_RECONSTRUCTION_ACCEPTANCE.md',cases=r['cases'],rows=rows,release_approved=False)
a.output.write_text(json.dumps(result,indent=2));print(json.dumps(rows,indent=2))
