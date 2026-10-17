"""Real native refusal tests; mutations apply only to disposable test copies."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import numpy as np


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--valid-report',type=Path,required=True)
    p.add_argument('--work-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();root=a.work_root.resolve();root.mkdir(parents=True,exist_ok=True)
    report=json.loads(a.valid_report.read_text());valid=Path(report['cases'][0]['session'])
    volumes=a.valid_report.parent/'uniform/volumes';records=[]
    cases=('version','roles','units','clock','missing_frame','corrupt_frame','nan_velocity','double_flow')
    for case in cases:
        with tempfile.TemporaryDirectory(prefix='hydro-refusal-',dir=root) as temporary:
            task=Path(temporary).resolve();assert task.is_relative_to(root)
            v=task/'volumes';shutil.copytree(volumes,v)
            session=task/'session';shutil.copytree(valid,session,ignore=shutil.ignore_patterns('output'))
            meta=json.loads((v/'hydrodynamics.json').read_text())
            if case=='version':meta['version']=2
            if case=='roles':meta['roles'].reverse()
            if case=='units':meta['units']='centimetres'
            (v/'hydrodynamics.json').write_text(json.dumps(meta))
            if case=='clock':
                text=(v/'force_field.toml').read_text();parts=text.rsplit('step = 0.1',1)
                (v/'force_field.toml').write_text('step = 0.2'.join(parts))
            if case=='missing_frame':(v/'field-1-fluid/bricks-00000.bin').unlink()
            if case=='corrupt_frame':
                with (v/'field-1-fluid/bricks-00000.bin').open('ab') as f:f.write(b'x')
            if case=='nan_velocity':
                path=v/'field-0-fluid/bricks-00000.bin';data=np.fromfile(path,dtype='<f4');data[0]=np.nan;data.tofile(path)
            if case=='double_flow':
                legacy=session/'bin/force_field';shutil.copytree(v,legacy)
                path=legacy/'field-0-track.bin';data=np.fromfile(path,dtype='<f4');data[9]=1;data.tofile(path)
            config=task/'config.json';config.write_text(json.dumps(dict(density_kg_m3=1000,
                normal_coefficient=1,tangential_coefficient=0,wet_area_fraction=1,
                velocity_m_s=[0,0,0],volume_mode=1)))
            env=os.environ.copy();env['PPF_EXPERIMENTAL_HYDRO_TEST_JSON']=str(config)
            env['PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR']=str(v)
            run=subprocess.run([str(a.solver.resolve()),'--path',str(session),'--output',str(task/'output')],
                env=env,capture_output=True,text=True,timeout=30)
            text=run.stdout+run.stderr
            assert run.returncode!=0,(case,text)
            if case=='double_flow':assert 'simultaneous legacy Fluid Flow' in text,text
            records.append(dict(case=case,exit_code=run.returncode,diagnostic=text[-1800:]))
            print(case,run.returncode,flush=True)
    a.output.write_text(json.dumps(dict(result='PASS',cases=records),indent=2))


if __name__=='__main__':main()
