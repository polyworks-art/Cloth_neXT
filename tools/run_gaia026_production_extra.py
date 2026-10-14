"""Serialize supplemental workflow, RAM and recurrent-contact gates."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True)
p.add_argument('--solver',type=Path,required=True)
a=p.parse_args()
deadline=time.monotonic()+43200
while not (a.root/'followup-status.json').is_file():
    if time.monotonic()>deadline:raise TimeoutError('Owned followup did not finish')
    time.sleep(5)
status=json.loads((a.root/'followup-status.json').read_text(encoding='utf-8-sig'))
assert status['returncode']==0,status
report=dict(result='RUNNING',gates=[])
def save(): (a.root/'extra-report.json').write_text(json.dumps(report,indent=2))
def execute(name,argv):
    started=time.monotonic()
    with (a.root/(name+'.log')).open('wb') as log:
        result=subprocess.run(list(map(str,argv)),cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            timeout=14400,creationflags=subprocess.CREATE_NO_WINDOW)
    report['gates'].append(dict(name=name,returncode=result.returncode,seconds=time.monotonic()-started))
    save();print(name,result.returncode,flush=True)
blender=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe')
source=Path(r'F:\16_Cloth_neXt\Flip_Test.blend')
common=[blender,'--background','--factory-startup','--disable-autoexec','--python-exit-code','1',
        '--python',ROOT/'tools/blender_gaia026_production_scale.py','--','--repo',ROOT,
        '--solver',a.solver,'--fluid-source',source,'--vertices','1000','--last','12',
        '--strength','.05','--timeout','1800']
execute('workflow-rebake-error-preservation',common+['--output',a.root/'workflow-rebake-error',
    '--gaia',a.root/'matrix/fields/12.gaia','--rebake','--verify-convergence-failure'])
driver=[sys.executable,ROOT/'tools/run_gaia026_production_scale.py','--solver',a.solver,
    '--gaia',a.root/'matrix/fields/250.gaia']
quality_options=['--geometry-only','--time-step','.001','--cg-max-iter','10000','--cg-tol','.001']
quality_report=json.loads((a.root/'quality-matrix/report.json').read_text())
if not any(x['vertices_per_cloth']==20000 and x['frames']==3 and x['result']=='PASS' for x in quality_report['cases']):
    execute('20000-quality-retry',driver+['--output',a.root/'quality-retry-20000','--sizes','20000',*quality_options])
if not any(x['vertices_per_cloth']==50000 and x['frames']==250 and x['result']=='PASS' for x in quality_report['cases']):
    execute('50000-250-actual',driver+['--output',a.root/'quality-50000-250','--sizes','50000','--frames','250',*quality_options])
execute('5000-250-resource-confirm',driver+['--output',a.root/'resource-confirm',
    '--sizes','5000','--frames','250','--geometry-only'])
execute('recurrent-collider-sweep',driver+['--output',a.root/'sweep-matrix','--sizes','1000',
    '--frames','3','--combinations','zones','sweep','--time-step','.001','--cg-max-iter','10000','--cg-tol','.001'])
for vertices in [1000,5000]:
    execute(f'dry-control-{vertices}',[blender,'--background','--factory-startup','--disable-autoexec',
        '--python-exit-code','1','--python',ROOT/'tools/blender_gaia026_production_scale.py','--',
        '--repo',ROOT,'--solver',a.solver,'--fluid-source',source,'--output',a.root/f'dry-control-{vertices}',
        '--vertices',str(vertices),'--last','60','--dry','--time-step','.01','--cg-max-iter','2500','--cg-tol','.01',
        '--timeout','1800'])
execute('two-cloth-250-retry',[blender,'--background','--factory-startup','--disable-autoexec',
    '--python-exit-code','1','--python',ROOT/'tools/blender_gaia026_production_scale.py','--',
    '--repo',ROOT,'--solver',a.solver,'--fluid-source',source,'--output',a.root/'two-cloth-250-retry',
    '--vertices','1000','--last','250','--combination','two','--strength','.2',
    '--gaia',a.root/'matrix/fields/250.gaia','--timeout','1800'])
execute('folded-retry',driver+['--output',a.root/'folded-retry','--sizes','1000','--frames','3',
    '--combinations','folded','--time-step','.001','--cg-max-iter','10000','--cg-tol','.001'])
report['result']='PASS' if all(x['returncode']==0 for x in report['gates']) else 'FAIL'
save()
