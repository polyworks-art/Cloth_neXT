"""Independent full native trajectory controls; no release or acceptance override."""
import argparse,os,json,hashlib,subprocess,time
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--mode',choices=['topology','ordered'],required=True);p.add_argument('--runs',type=int,default=5);a=p.parse_args()
root=Path.cwd();b=root/'.hydro-test-tmp/reproducibility-20261017';exe=b/'native-source/target/research-cuda/release/ppf-contact-solver.exe';fixture=b/'independent-canvas/fixture';fluid=b/'independent-canvas';folder=b/'full-independent-cached-five';folder.mkdir()
lib=b/'cached-cuda-build/lib'
env={k:v for k,v in os.environ.items() if not k.startswith('PPF_REPRO_')}
env.update(PPF_EXPERIMENTAL_HYDRO_TEST_JSON=str(fluid/'model.json'),PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=str(fluid/'volumes'));env['PATH']=str(lib)+os.pathsep+str(root/'.hydro-test-tmp/blender-e2e-20261016/runtime/bin')+os.pathsep+env['PATH']
if a.mode=='ordered':env.update(PPF_REPRO_ORDER_MODE='colored',PPF_REPRO_ORDER_FILTER='all',PPF_REPRO_FILTERED_CONTACT='1',PPF_REPRO_SERIAL_CSR='1',PPF_REPRO_SERIAL_STITCH='1')
inputs={str(q.relative_to(fixture)):hashlib.sha256(q.read_bytes()).hexdigest() for q in sorted(fixture.rglob('*')) if q.is_file() and (q.suffix=='.bin' or q.name=='param.toml')}
manifest=dict(mode=a.mode,worker_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),cuda_sha256=hashlib.sha256((lib/'libsimbackend_cuda.dll').read_bytes()).hexdigest(),inputs=inputs,physical_parameters='frozen independent 441-vertex canvas / animated plane; Cn=1 Ct=0; full uniform fluid interior',probe_readbacks=False,scope='native repeatability control; Blender playback and collision audits are separate required gates',runs=[])
(folder/'manifest.json').write_text(json.dumps(manifest,indent=2))
for i in range(a.runs):
 case=folder/str(i);case.mkdir();samples=[];start=time.perf_counter();last=-1
 with (case/'worker.log').open('w') as log:
  run=subprocess.Popen([str(exe),'--path',str(fixture),'--output',str(case/'output')],env=env,stdout=log,stderr=subprocess.STDOUT)
  while run.poll() is None:
   now=time.perf_counter()-start
   if not samples or now-samples[-1]['seconds']>=1:
    result=subprocess.run(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True,capture_output=True)
    values=[float(s.strip()) for s in result.stdout.splitlines() if s.strip().isdigit()]
    samples.append(dict(seconds=now,total_device_memory_mib=values))
   frames=len(list((case/'output').glob('vert_*.bin')))
   if frames!=last:print(a.mode,i,'vertex outputs',frames,'seconds',round(now,2),flush=True);last=frames
   time.sleep(.25)
 elapsed=time.perf_counter()-start;files=sorted((case/'output').glob('vert_*.bin'));finite=all(np.isfinite(np.fromfile(q,dtype='<f4')).all() for q in files)
 result=dict(run=i,exit_code=run.returncode,elapsed_seconds=elapsed,vertex_outputs=len(files),finite=bool(finite),device_memory_scope='whole GPU, not per-process VRAM',peak_total_device_memory_mib=max((max(s['total_device_memory_mib'],default=0) for s in samples),default=None))
 (case/'resources.json').write_text(json.dumps(samples));manifest['runs'].append(result);(folder/'manifest.json').write_text(json.dumps(manifest,indent=2));print(result,flush=True)
 assert run.returncode==0 and len(files)==60 and finite,result
print('All requested full native controls completed; further comparisons required.',flush=True)
