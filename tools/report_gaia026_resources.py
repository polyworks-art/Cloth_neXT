"""Summarize measured process peaks without confusing probes with native solves."""
import argparse,csv,json
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();rows=[]
for path in sorted(a.root.rglob('*-resources.json')):
    data=json.loads(path.read_text());roles={}
    for process in data.get('process_peaks',[]):
        argv=process.get('argv',[]);name=process.get('name','')
        if name=='blender.exe':role='blender_peak_bytes'
        elif '--host' in argv:role='server_peak_bytes'
        elif name=='ppf-contact-solver.exe' and '--path' in argv:role='native_solver_peak_bytes'
        elif any('build_worker.py' in arg for arg in argv):role='build_worker_peak_bytes'
        else:continue
        peak=process.get('os_peak_working_set_bytes')
        if peak is not None:roles[role]=max(roles.get(role,0),peak)
    rows.append(dict(case=str(path.relative_to(a.root)),returncode=data.get('returncode'),
        wall_seconds=data.get('wall_seconds'),**roles))
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('w',newline='',encoding='utf-8') as stream:
    writer=csv.DictWriter(stream,fieldnames=['case','returncode','wall_seconds','blender_peak_bytes',
        'server_peak_bytes','native_solver_peak_bytes','build_worker_peak_bytes'])
    writer.writeheader();writer.writerows(rows)
print(json.dumps(dict(cases=len(rows),scope='OS process peaks; blank means unavailable; probes excluded')))
