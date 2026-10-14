# SPDX-License-Identifier: GPL-3.0-or-later
"""Real server-loss recovery and full-cache reuse/resource confirmation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solver',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stability-report',type=Path,required=True)
    parser.add_argument('--long-report',type=Path,required=True)
    parser.add_argument('--blender',type=Path,default=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe'))
    parser.add_argument('--source',type=Path,default=Path(r'F:\16_Cloth_neXt\Flip_Test.blend'))
    args=parser.parse_args()
    assert json.loads(args.stability_report.read_text())['result']=='PASS'
    long=json.loads(args.long_report.read_text());assert long['result']=='PASS'
    args.output.mkdir(parents=True,exist_ok=False)
    report={'result':'RUNNING','gates':[]}
    def save(): (args.output/'report.json').write_text(json.dumps(report,indent=2))
    def execute(name,command):
        started=time.monotonic()
        with (args.output/(name+'.log')).open('wb') as log:
            result=subprocess.run(list(map(str,command)),cwd=ROOT,
                stdout=log,stderr=subprocess.STDOUT,timeout=1800,
                creationflags=subprocess.CREATE_NO_WINDOW)
        report['gates'].append({'name':name,'returncode':result.returncode,
            'seconds':time.monotonic()-started,'result':'PASS' if result.returncode==0 else 'FAIL'})
        save();print(name,result.returncode,flush=True)
        assert result.returncode==0,name
    try:
        for phase in ('crash','resume'):
            execute('fluid-server-loss-'+phase,[args.blender,'--background','--factory-startup',
                '--disable-autoexec','--python-exit-code','1','--python',
                ROOT/'tools/blender_recovery_integration.py','--','--repo',ROOT,
                '--solver',args.solver,'--phase',phase,'--blend',args.output/'crash-fixture.blend',
                '--cache',args.output/'crash-cache','--report',args.output/(phase+'.json'),
                '--fluid-source',args.source,'--subdivisions','6'])
        crash=json.loads((args.output/'crash.json').read_text())
        assert any(c['exists'] and c['integrity']=='VERIFIED' for c in crash['checkpoints'])
        assert json.loads((args.output/'resume.json').read_text())['terminal']=='finished'
        source=json.loads(Path(long['stages'][-1]['wet']['report']).read_text())
        python=args.solver.parent.parent.parent.parent/'python/python.exe'
        execute('250-cache-reuse-resource-confirm',[python,ROOT/'tools/run_gaia026_long_sequence.py',
            '--solver',args.solver,'--blender',args.blender,'--source',args.source,
            '--output',args.output/'250-confirm','--stages','250',
            '--gaia',source['prepared_gaia'],'--dry-report',long['stages'][-1]['dry']['report']])
        report['result']='PASS'
    except BaseException as error:
        report.update(result='FAIL',error=repr(error));raise
    finally:save()


if __name__=='__main__':main()
