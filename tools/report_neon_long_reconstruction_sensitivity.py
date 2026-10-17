"""Stream full-sequence native sensitivity against the regular operator reference."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from cloth_next.ppf.schema import envelope
plan=json.loads((a.root/'report.json').read_text());run=Path(plan['source_run']);verified=json.loads((run/'verification.json').read_text());assert verified['result']=='PASS';original=Path(verified['native_output']).parent;first,last=json.loads((run/'report.json').read_text())['range'];count=last-first+1
scene=next((run/'cache/.cloth_next_export/scene').rglob('scene.cbor'));objects={o['name']:o for part in envelope.loads_envelope(scene.read_bytes(),envelope.KIND_SCENE,schema_version=2) for o in part['object']};mapping=envelope.loads_envelope((original/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2);names=[n for n in objects if n.startswith('S04_NEON_Garments')];ids=[mapping[objects[n]['uuid']] for n in names]
tri=np.fromfile(original/'bin/tri.bin',dtype='<u8').reshape(-1,3);owner=np.full(max(max(x) for x in mapping.values())+1,-1)
for i,index in enumerate(ids):owner[index]=i
masks=[(owner[tri]==i).all(1) for i in range(len(names))]
def load(session):
 poses=[[] for n in names];loads=[[] for n in names]
 for k in range(count):
  raw=np.fromfile(session/f'output/vert_{k}.bin',dtype='<f4').reshape(-1,3)
  for i,index in enumerate(ids):poses[i].append(raw[index].astype(float))
 for k in range(1,count):
  v=np.fromfile(session/f'output/hydro_{k}.bin',dtype='<f4').reshape(-1,16).astype(float);assert np.isfinite(v).all()
  for i,mask in enumerate(masks):
   s=v[mask];loads[i].append([s[:,1].sum(),s[:,2].sum()])
 return dict(poses=[np.array(x) for x in poses],loads=[np.array(x) for x in loads])
data={'operator_reference':load(original)}
for case in plan['cases']:
 if case['finished'] and case['returncode']==0:data[case['label']]=load(Path(case['session']))
rows=[];pairs=[(n,'operator_reference') for n in data if n!='operator_reference']
if 'baseline2' in data:pairs.append(('baseline2','baseline1'))
for label,reference in pairs:
 for i,name in enumerate(names):
  d=np.linalg.norm(data[label]['poses'][i]-data[reference]['poses'][i],axis=2);b=data[reference]['loads'][i];c=data[label]['loads'][i];error=abs(c-b);wet_ok=bool((error[:,0]<=np.maximum(.005,.05*b[:,0])).all());force_ok=bool((error[:,1]<=np.maximum(1.,.05*b[:,1])).all());temporal=float(abs(np.diff(c[:,1])-np.diff(b[:,1])).max());rms=float(np.sqrt(np.mean(d**2)));maximum=float(d.max())
  rows.append(dict(case=label,reference=reference,object=name,trajectory_rms_m=rms,trajectory_max_m=maximum,trajectory_screen='PASS' if rms<=.002 and maximum<=.008 else 'INVESTIGATE',wet_area_delta_max_m2=float(error[:,0].max()),wet_area_screen='PASS' if wet_ok else 'INVESTIGATE',normal_load_delta_max_n=float(error[:,1].max()),normal_load_screen='PASS' if force_ok else 'INVESTIGATE',normal_load_first_difference_delta_max_n=temporal,temporal_screen='PASS' if temporal<=max(1.,.1*float(b[:,1].mean())) else 'INVESTIGATE',per_frame_wet_area_m2=c[:,0].tolist(),per_frame_normal_load_n=c[:,1].tolist()))
peaks={n:dict(frame=first+1+int(np.argmax(sum(v['loads'][i][:,1] for i in range(len(names))))),total_load_n=float(max(sum(v['loads'][i][:,1] for i in range(len(names)))))) for n,v in data.items()}
result=dict(scope='Full30-frame actual native replays against regular Blender operator reference; preserved original material/attachments. Interface shifts are conservative sensitivity tests only.',criteria='docs/GAIA_PRACTICAL_RECONSTRUCTION_ACCEPTANCE.md',completed_cases=len(data)-1,expected_cases=4,peak_loads=peaks,rows=rows,release_approved=False)
a.output.write_text(json.dumps(result,indent=2));print(json.dumps({'completed_cases':result['completed_cases'],'peak_loads':peaks,'screens':[{k:v for k,v in r.items() if not k.startswith('per_frame')} for r in rows]},indent=2))
