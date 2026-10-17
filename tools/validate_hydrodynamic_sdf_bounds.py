"""Two native safety checks on disposable copies of a validated uniform scene."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import numpy as np


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    source=Path(json.loads((a.reference/'report.json').read_text())['cases'][0]['session'])
    records=[]
    for case in ('valid','outside'):
        root=a.output/case;root.mkdir()
        session=root/'session';shutil.copytree(source,session,ignore=shutil.ignore_patterns('output'))
        volumes=root/'volumes';shutil.copytree(a.reference/'uniform/volumes',volumes)
        if case=='outside':
            path=volumes/'field-1-fluid/transform.bin'
            transforms=np.fromfile(path,dtype='<f8').reshape(-1,15)
            transforms[:,0]+=10.;transforms.tofile(path)
        config=root/'model.json'
        config.write_text(json.dumps(dict(density_kg_m3=1000,normal_coefficient=1,
            tangential_coefficient=0,wet_area_fraction=1,velocity_m_s=[0,0,0],volume_mode=1)))
        env=os.environ.copy();env.update(PPF_EXPERIMENTAL_HYDRO_TEST_JSON=str(config.resolve()),
            PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=str(volumes.resolve()))
        with (root/'native.log').open('w',encoding='utf-8') as stream:
            run=subprocess.run([str(a.solver),'--path',str(session.resolve()),'--output',str((root/'output').resolve())],
                env=env,stdout=stream,stderr=subprocess.STDOUT,text=True,timeout=60)
        log=(root/'native.log').read_text(encoding='utf-8')
        if case=='valid':assert run.returncode==0,log[-3000:]
        else:
            assert run.returncode!=0, 'Out-of-bounds SDF silently accepted'
            assert 'sdf_valid' in log or 'face_force.kernel.cpp' in log,log[-3000:]
        records.append(dict(case=case,exit_code=run.returncode,result='PASS',diagnostic=log[-3000:]))
    (a.output/'report.json').write_text(json.dumps(dict(result='PASS',cases=records),indent=2))
    print(json.dumps(records,indent=2))


if __name__=='__main__':main()
