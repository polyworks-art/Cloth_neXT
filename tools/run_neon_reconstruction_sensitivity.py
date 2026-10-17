"""Actual unchanged native CUDA solver sensitivity on preserved reference session."""
import argparse,hashlib,json,os,shutil,subprocess,time
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--solver',type=Path,required=True);p.add_argument('--source-run',type=Path,default=Path('.hydro-test-tmp/blender-e2e-20261016/test-a-131-133-r9'));p.add_argument('--fine-volumes',type=Path);p.add_argument('--long-sequence',action='store_true');a=p.parse_args();a.output=a.output.resolve();a.output.mkdir(exist_ok=False,parents=True)
verification=json.loads((a.source_run/'verification.json').read_text());source=Path(verification['native_output']).parent
fine=(a.fine_volumes or Path('.hydro-test-tmp/flip-grid-edge-transport-131-134')).resolve();coarse=Path('.hydro-test-tmp/flip-grid-edge-transport-131-134-scale2').resolve()
plan={'source_session':str(source),'solver_sha256':hashlib.sha256(a.solver.read_bytes()).hexdigest(),'cases':[],'criteria':'docs/GAIA_PRACTICAL_RECONSTRUCTION_ACCEPTANCE.md','original_scene_settings_changed':False,'model':{'Cn':1,'Ct':0,'one_way':True},'scope':'Native sensitivity only, not a regular Blender operator bake','source_run':str(a.source_run.resolve())}
(a.output/'report.json').write_text(json.dumps(plan,indent=2))
cases=[('baseline1',0,fine),('baseline2',0,fine),('sdf_plus_2p5mm',.0025,fine),('sdf_minus_2p5mm',-.0025,fine)]
if not a.long_sequence:cases.append(('coarse_15p65mm',0,coarse))
for label,offset,volume in cases:
 case=a.output/label;case.mkdir();session=case/'session';shutil.copytree(source,session,ignore=shutil.ignore_patterns('output','logs'))
 if (session/'bin/force_field').exists():raise RuntimeError('Unexpected legacy fluid flow')
 if offset:
  copied=case/'volumes';shutil.copytree(volume,copied)
  for f in (copied/'field-1-fluid').glob('bricks-*.bin'):
   values=np.fromfile(f,dtype='<f4').reshape(-1,4);values[:,0]+=offset;values.tofile(f)
  volume=copied
 model=case/'model.json';model.write_text(json.dumps(dict(density_kg_m3=1000.,normal_coefficient=1.,tangential_coefficient=0.,wet_area_fraction=1.,velocity_m_s=[0,0,0],volume_mode=1)))
 env=os.environ.copy();env['PPF_EXPERIMENTAL_HYDRO_TEST_JSON']=str(model);env['PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR']=str(volume);env['PATH']=str(Path('.hydro-test-tmp/blender-e2e-20261016/runtime/bin').resolve())+os.pathsep+env.get('PATH','')
 started=time.monotonic();print(label,'START',flush=True)
 with (case/'native.log').open('w') as log:run=subprocess.run([str(a.solver.resolve()),'--path',str(session),'--output',str(session/'output')],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600)
 row=dict(label=label,sdf_offset_m=offset,seconds=time.monotonic()-started,returncode=run.returncode,finished=(session/'output/finished.txt').exists(),volume=str(volume),session=str(session))
 plan['cases'].append(row);(a.output/'report.json').write_text(json.dumps(plan,indent=2));print(row,flush=True)
 if run.returncode or not row['finished']:raise RuntimeError(f'Native sensitivity {label} failed')
