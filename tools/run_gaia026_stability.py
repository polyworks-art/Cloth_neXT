# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial real runtime regression gates after the full native sequence gate."""
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
    parser.add_argument('--long-report',type=Path,required=True)
    parser.add_argument('--blender',type=Path,default=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe'))
    parser.add_argument('--source',type=Path,default=Path(r'F:\16_Cloth_neXt\Flip_Test.blend'))
    args=parser.parse_args()
    long=json.loads(args.long_report.read_text())
    assert long['result']=='PASS' and [s['frames'] for s in long['stages']]==[3,24,60,120,250]
    args.output.mkdir(parents=True,exist_ok=False)
    report={'result':'RUNNING','gates':[]}
    def save(): (args.output/'report.json').write_text(json.dumps(report,indent=2))
    def execute(name,command,environment=None):
        start=time.monotonic()
        with (args.output/(name+'.log')).open('wb') as log:
            result=subprocess.run(list(map(str,command)),cwd=ROOT,env=environment,
                stdout=log,stderr=subprocess.STDOUT,timeout=2400,
                creationflags=subprocess.CREATE_NO_WINDOW)
        report['gates'].append({'name':name,'returncode':result.returncode,
            'seconds':time.monotonic()-start,'result':'PASS' if result.returncode==0 else 'FAIL'})
        save();print(name,result.returncode,flush=True)
        assert result.returncode==0,name
    def blender(script,name,options):
        execute(name,[args.blender,'--background','--factory-startup','--disable-autoexec',
            '--python-exit-code','1','--python',ROOT/'tools'/script,'--',*options])
    try:
        blender('blender_gaia_regular_bake_probe.py','cache-integrity',[
            '--repo',ROOT,'--solver',args.solver,'--output',args.output/'cache-integrity',
            '--fluid-source',args.source,'--last','3','--timeout','300',
            '--verify-reuse','--verify-corruption'])
        for phase in ('cancel','resume'):
            blender('blender_recovery_integration.py','fluid-'+phase,[
                '--repo',ROOT,'--solver',args.solver,'--phase',phase,
                '--blend',args.output/'fluid-recovery.blend','--cache',args.output/'fluid-recovery',
                '--report',args.output/('fluid-'+phase+'.json'),'--fluid-source',args.source,
                '--subdivisions','6'])
        execute('lifecycle',[sys.executable,ROOT/'tools/run_gaia_windows_lifecycle.py',
            '--solver',args.solver,'--expected-protocol','0.26','--blender',args.blender,
            '--output-dir',args.output/'lifecycle','--frames','8','--recovery-subdivisions','6'])
        environment=dict(os.environ);environment['CLOTH_NEXT_PPF_EXECUTABLE']=str(args.solver)
        execute('configured-integration',[sys.executable,'-m','pytest','tests/integration','-q'],environment)
        blender('blender_collider_attachments_smoke.py','collider-attachments',[
            '--solver',args.solver,'--output',args.output/'collider-attachments'])
        execute('idle-keepalive',[sys.executable,ROOT/'tools/probe_gaia_server_idle.py',
            '--solver',args.solver,'--output',args.output/'idle-keepalive'])
        report['result']='PASS'
    except BaseException as error:
        report.update(result='FAIL',error=repr(error));raise
    finally:save()


if __name__=='__main__':main()
