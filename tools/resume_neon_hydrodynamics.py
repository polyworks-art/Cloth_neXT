"""Resume stopped CPU NEON cases in disposable copies of their native sessions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.ppf.schema import envelope


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frontend-root',type=Path,required=True)
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--volumes',type=Path,required=True)
    p.add_argument('--sessions',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--jobs',type=int,default=5)
    a=p.parse_args()
    sys.path.insert(0,str(a.frontend_root));from frontend import App,_rust
    App.set_backend('cuda')
    evidence=ROOT/'docs/validation/gaia026/hydrodynamics'
    baseline=json.loads((evidence/'neon-spatial-cuda30/report.json').read_text())
    build=json.loads((evidence/'cuda-build-evidence.json').read_text(encoding='utf-8-sig'))
    digest=hashlib.sha256(a.solver.read_bytes()).hexdigest()
    if digest.lower()!=build['cpu_solver_sha256'].lower():raise ValueError('CPU executable changed')
    source=json.loads((evidence/'user-stopped-processes.json').read_text(encoding='utf-8-sig'))
    origins={}
    for proc in source:
        if not proc['Name'].startswith('ppf-'):continue
        session=Path(re.search(r'--path (.+?) --output',proc['CommandLine']).group(1))
        label=re.search(r'cnx_spatial_neon_(\w+)_\d+',str(session)).group(1)
        origins[label]=session
    if set(origins)!={'hydro1','hydro2','dry1','dry2','flow1'}:raise ValueError('Incomplete stopped case set')
    a.sessions.mkdir(parents=True,exist_ok=False);a.output.mkdir(parents=True,exist_ok=False)
    report={**baseline,'solver':str(a.solver.resolve()),'solver_sha256':digest,'backend':'cpu',
            'checkpoint_resume':True,'cases':[]}
    lock=threading.Lock()
    plan=json.loads((Path(baseline['source_scene']).parent/'metadata.json').read_text())['plan']

    def run(label):
        original=origins[label];mode=label.rstrip('0123456789')
        payload=envelope.loads_envelope((original.parent/'param.pickle').read_bytes(),envelope.KIND_PARAM,schema_version=2)
        if payload['scene']!=baseline['quality']:raise ValueError(f'{label}: quality differs')
        states=list((original/'output').glob('state_*.bin.gz'))
        checkpoint=max(int(re.search(r'state_(\d+)',s.name).group(1)) for s in states)
        if checkpoint<=0 or checkpoint>=baseline['frames']-1:raise ValueError('Unexpected checkpoint')
        folder=a.output/label;folder.mkdir()
        session=a.sessions/label/'session'
        print(label,'COPY',original,'checkpoint',checkpoint,flush=True)
        shutil.copytree(original,session)
        for f in (session/'output').glob('hydro_*'):
            m=re.match(r'hydro_(\d+)\.',f.name)
            if m and int(m.group(1))>checkpoint:f.unlink()
        env=os.environ.copy()
        env.pop('PPF_EXPERIMENTAL_HYDRO_TEST_JSON',None);env.pop('PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR',None)
        if mode=='hydro':
            config=folder/'input.json'
            config.write_text(json.dumps(dict(density_kg_m3=1000.,normal_coefficient=1.,
                tangential_coefficient=0.,wet_area_fraction=1.,velocity_m_s=[0,0,0],volume_mode=1)))
            env['PPF_EXPERIMENTAL_HYDRO_TEST_JSON']=str(config.resolve())
            env['PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR']=str(a.volumes.resolve())
            if (session/'bin/force_field').exists():raise ValueError('Double coupling')
        command=[str(a.solver.resolve()),'--path',str(session.resolve()),'--output',str((session/'output').resolve()),'--load',str(checkpoint)]
        manifest=dict(original_session=str(original),checkpoint=checkpoint,solver_sha256=digest,command=command)
        (folder/'resume.json').write_text(json.dumps(manifest,indent=2))
        started=time.monotonic();print(label,'RESUME',flush=True)
        with (folder/'native.log').open('w') as log:
            result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=7200)
        if result.returncode or not (session/'output/finished.txt').exists():raise RuntimeError(f'{label}: native resume failed')
        poses=np.asarray([_rust.read_vertex_bin(str(session/'output'),i) for i in range(baseline['frames'])])
        if poses.shape!=(baseline['frames'],77860,3) or not np.isfinite(poses).all():raise ValueError('Invalid resumed trajectories')
        mapping=envelope.loads_envelope((session/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2)
        np.save(folder/'native-positions.npy',poses);shutil.copyfile(session/'map.pickle',folder/'map.cbor')
        for target in plan['targets']:np.save(folder/(target['name']+'.npy'),poses[:,mapping[target['uuid']]])
        old=next(c for c in baseline['cases'] if c['label']==label)
        record={**old,'session':str(session.resolve()),'seconds':time.monotonic()-started,
                'seconds_scope':'resumed segment only; earlier compute excluded',
                'shape':list(poses.shape),'finite':True,'resume':manifest,'log_scope':'resumed segment only'}
        with lock:
            report['cases'].append(record)
            (a.output/'report.json').write_text(json.dumps(report,indent=2))
        print(label,'FINISHED',record['seconds'],flush=True)

    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        list(pool.map(run,sorted(origins)))


if __name__=='__main__':main()
