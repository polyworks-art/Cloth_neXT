"""Final serialized retries after supplemental native jobs complete."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True);p.add_argument('--solver',type=Path,required=True)
a=p.parse_args();deadline=time.monotonic()+57600
while not (a.root/'extra-status.json').is_file():
    if time.monotonic()>deadline:raise TimeoutError('Owned extra job did not finish')
    time.sleep(5)
report=dict(result='RUNNING',gates=[])
def save(): (a.root/'remaining-report.json').write_text(json.dumps(report,indent=2))
def run(name,argv,env=None):
    started=time.monotonic()
    with (a.root/(name+'.log')).open('wb') as log:
        r=subprocess.run(list(map(str,argv)),cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
            timeout=28800,creationflags=subprocess.CREATE_NO_WINDOW)
    report['gates'].append(dict(name=name,returncode=r.returncode,seconds=time.monotonic()-started))
    save();print(name,r.returncode,flush=True)
system_python=Path(os.environ['LOCALAPPDATA'])/'Programs/Python/Python311/python.exe'
env=dict(os.environ);env['CLOTH_NEXT_PPF_EXECUTABLE']=str(a.solver)
run('configured-integration-system',[system_python,'-m','pytest','tests/integration','-q'],env)
blender=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe')
source=Path(r'F:\16_Cloth_neXt\Flip_Test.blend')
base=[blender,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1',
      '--python',ROOT/'tools/blender_gaia026_production_scale.py','--','--repo',ROOT,'--solver',a.solver,
      '--fluid-source',source,'--timeout','1800']
run('two-cloth-60-sharing',base+['--output',a.root/'two-cloth-60-sharing','--vertices','1000',
    '--last','60','--combination','two','--strength','.2','--gaia',a.root/'matrix/fields/60.gaia',
    '--time-step','.01','--cg-max-iter','2500','--cg-tol','.01'])
try:
    import numpy as np
    from cloth_next.bake.pc2 import iter_frames
    old=json.loads((a.root/'matrix/1000-60-two-0.2-wet/report.json').read_text())
    new=json.loads((a.root/'two-cloth-60-sharing/report.json').read_text())
    maximum=0.
    for before,after in zip(old['objects'],new['objects']):
        x=np.asarray(list(iter_frames(Path(before['pc2']))))
        y=np.asarray(list(iter_frames(Path(after['pc2']))))
        maximum=max(maximum,float(np.linalg.norm(x-y,axis=2).max()))
    report['shared_volume_comparison']=dict(result='PASS' if maximum<1e-4 else 'FAIL',
        maximum_position_difference_m=maximum,tolerance_m=1e-4,
        original_param_bytes=old['native_file_metrics'][0]['param_bytes'],
        shared_param_bytes=new['native_file_metrics'][0]['param_bytes'])
except Exception as exc:report['shared_volume_comparison']=dict(result='FAIL',error=repr(exc))
save()
run('workflow-preservation-fixed',base+['--output',a.root/'workflow-preservation-fixed',
    '--vertices','1000','--last','12','--strength','.05','--gaia',a.root/'matrix/fields/12.gaia',
    '--rebake','--verify-convergence-failure'])
driver=[sys.executable,ROOT/'tools/run_gaia026_production_scale.py','--solver',a.solver,
        '--gaia',a.root/'matrix/fields/250.gaia']
quality=['--geometry-only','--time-step','.001','--cg-max-iter','10000','--cg-tol','.001']
run('20000-quality-retry',driver+['--output',a.root/'quality-retry-20000','--sizes','20000',*quality])
run('50000-250-actual',driver+['--output',a.root/'quality-50000-250','--sizes','50000','--frames','250',*quality])
for vertices in [1000,5000]:
    run(f'dry-control-{vertices}',base+['--output',a.root/f'dry-control-{vertices}',
        '--vertices',str(vertices),'--last','60','--dry','--time-step','.01','--cg-max-iter','2500','--cg-tol','.01'])
run('two-cloth-250-retry',base+['--output',a.root/'two-cloth-250-retry','--vertices','1000',
    '--last','250','--combination','two','--strength','.2','--gaia',a.root/'matrix/fields/250.gaia'])
run('folded-retry',driver+['--output',a.root/'folded-retry','--sizes','1000','--frames','3',
    '--combinations','folded','--time-step','.001','--cg-max-iter','10000','--cg-tol','.001'])
report['result']='PASS' if all(x['returncode']==0 for x in report['gates']) else 'FAIL';save()
