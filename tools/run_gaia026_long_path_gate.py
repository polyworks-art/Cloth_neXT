"""Retest the actual failed long recovery path after the serialized finish queue."""
import argparse,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True);p.add_argument('--solver',type=Path,required=True)
a=p.parse_args();deadline=time.monotonic()+43200
while not (a.root/'finish-status.json').is_file():
    if time.monotonic()>deadline:raise TimeoutError('Serialized finish queue did not finish')
    time.sleep(5)
output=a.root/'quality-50000-long-path-fixed'
argv=[sys.executable,str(ROOT/'tools/run_gaia026_production_scale.py'),'--solver',str(a.solver),
    '--output',str(output),'--sizes','50000','--frames','250','--geometry-only',
    '--time-step','.001','--cg-max-iter','10000','--cg-tol','.001']
with (a.root/'long-path-fixed.log').open('wb') as log:
    result=subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
        timeout=6000,creationflags=subprocess.CREATE_NO_WINDOW)
report=json.loads((output/'report.json').read_text())
(a.root/'long-path-fixed-report.json').write_text(json.dumps(dict(returncode=result.returncode,
    result=report['result'],report=str(output/'report.json')),indent=2))
