"""Wait for the owned matrix, then serialize remaining native validation jobs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True)
p.add_argument('--solver',type=Path,required=True)
a=p.parse_args()
deadline=time.monotonic()+28800
while not (a.root/'matrix-status.json').is_file():
    if time.monotonic()>deadline:raise TimeoutError('Owned matrix did not finish')
    time.sleep(5)
status=json.loads((a.root/'matrix-status.json').read_text(encoding='utf-8-sig'))
assert status['returncode']==0,status
report=dict(result='RUNNING',gates=[])
def save(): (a.root/'followup-report.json').write_text(json.dumps(report,indent=2))
def execute(name,argv,env=None):
    started=time.monotonic()
    with (a.root/(name+'.log')).open('wb') as log:
        result=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=log,
            stderr=subprocess.STDOUT,timeout=14400,creationflags=subprocess.CREATE_NO_WINDOW)
    report['gates'].append(dict(name=name,returncode=result.returncode,seconds=time.monotonic()-started))
    save();print(name,result.returncode,flush=True)
save()
job=json.loads((a.root/'quality-matrix-job.json').read_text(encoding='utf-8-sig'))
execute('quality-matrix',job['argv'])
env=dict(os.environ);env['CLOTH_NEXT_PPF_EXECUTABLE']=str(a.solver)
execute('configured-integration',[sys.executable,'-m','pytest','tests/integration','-q'],env)
report['result']='PASS' if all(x['returncode']==0 for x in report['gates']) else 'FAIL'
save()
