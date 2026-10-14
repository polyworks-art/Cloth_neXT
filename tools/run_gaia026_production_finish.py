"""Fresh uninterrupted scale gates and scene dry control after the existing queue."""
import argparse,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True);p.add_argument('--solver',type=Path,required=True)
a=p.parse_args();deadline=time.monotonic()+43200
while not (a.root/'remaining-status.json').is_file():
    if time.monotonic()>deadline:raise TimeoutError('Existing production queue did not finish')
    time.sleep(5)
report=dict(result='RUNNING',gates=[])
def save():(a.root/'finish-report.json').write_text(json.dumps(report,indent=2))
def run(name,argv,result_path):
    started=time.monotonic()
    with (a.root/(name+'.log')).open('wb') as log:
        result=subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            timeout=21600,creationflags=subprocess.CREATE_NO_WINDOW)
    data=json.loads(result_path.read_text()) if result_path.is_file() else {}
    report['gates'].append(dict(name=name,returncode=result.returncode,
        result=data.get('result','FAIL'),seconds=time.monotonic()-started,report=str(result_path)))
    save();print(name,report['gates'][-1]['result'],flush=True)
save()
driver=[sys.executable,ROOT/'tools/run_gaia026_production_scale.py','--solver',a.solver,'--geometry-only']
output=a.root/'quality-50000-uninterrupted'
run('50000-uninterrupted',driver+['--output',output,'--sizes','50000','--frames','250',
    '--time-step','.001','--cg-max-iter','10000','--cg-tol','.001'],output/'report.json')
output=a.root/'quality-20000-stricter'
run('20000-stricter',driver+['--output',output,'--sizes','20000',
    '--time-step','.0005','--cg-max-iter','20000','--cg-tol','.0001'],output/'report.json')
job=json.loads((a.root/'neon-streamed-128-3-world-job.json').read_text(encoding='utf-8-sig'))
argv=list(job['argv']);output=a.root/'neon-streamed-dry-60'
argv[argv.index('--output')+1]=str(output);argv[argv.index('--last')+1]='60'
argv[argv.index('--timeout')+1]='5400';argv.append('--dry')
run('neon-dry-60',argv,output/'report.json')
report['result']='PASS' if all(g['result']=='PASS' and not g['returncode'] for g in report['gates']) else 'FAIL'
save()
