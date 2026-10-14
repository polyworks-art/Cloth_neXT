"""Run longer native NEON gates only after successful streamed short validation."""
import argparse,atexit,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True);p.add_argument('--solver',type=Path,required=True)
p.add_argument('--resume-pids',type=int,nargs='+',default=[])
a=p.parse_args()
if a.resume_pids:
    import psutil
    def resume():
        for pid in a.resume_pids:
            try:
                process=psutil.Process(pid)
                if any(s in ' '.join(process.cmdline()) for s in ['gaia026_production','ppf-cts-server.exe']):
                    process.resume()
            except psutil.NoSuchProcess:pass
    atexit.register(resume)
short=json.loads((a.root/'neon-streamed-128-3-world/report.json').read_text())
assert short['result']=='PASS'
job=json.loads((a.root/'neon-streamed-128-3-world-job.json').read_text(encoding='utf-8-sig'))
report=dict(result='RUNNING',cases=[],unexecuted=[])
def save():(a.root/'neon-streamed-long-report.json').write_text(json.dumps(report,indent=2))
save()
for frames in [60,250]:
    output=a.root/f'neon-streamed-128-{frames}'
    argv=list(job['argv'])
    argv[argv.index('--output')+1]=str(output)
    argv[argv.index('--last')+1]=str(frames)
    argv[argv.index('--timeout')+1]='10800'
    started=time.monotonic()
    with (a.root/f'neon-streamed-128-{frames}.log').open('wb') as log:
        result=subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            timeout=10980,creationflags=subprocess.CREATE_NO_WINDOW)
    case=json.loads((output/'report.json').read_text()) if (output/'report.json').is_file() else {}
    report['cases'].append(dict(frames=frames,result=case.get('result','FAIL'),
        returncode=result.returncode,wall_seconds=time.monotonic()-started,report=str(output/'report.json')))
    save();print(frames,report['cases'][-1]['result'],flush=True)
    if result.returncode or case.get('result')!='PASS':
        if frames==60:report['unexecuted'].append(dict(frames=250,reason='60-frame gate failed'))
        break
report['result']='PASS' if len(report['cases'])==2 and all(c['result']=='PASS' for c in report['cases']) else 'FAIL'
save()
