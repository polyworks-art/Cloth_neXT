# SPDX-License-Identifier: GPL-3.0-or-later
"""Retry the corrected cleanup assertion and finish remaining real regression gates."""
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
    parser.add_argument('--previous',type=Path,required=True)
    parser.add_argument('--blender',type=Path,default=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe'))
    args=parser.parse_args()
    previous=json.loads((args.previous/'report.json').read_text())
    failed=[g for g in previous['gates'] if g['result']=='FAIL']
    assert [g['name'] for g in failed]==['configured-integration'],failed
    log=(args.previous/'configured-integration.log').read_text(errors='replace')
    assert '3 failed, 10 passed' in log and 'test_phase3a_vertical_slice.py::test_real_ppf_vertical_slice' in log
    args.output.mkdir(parents=True,exist_ok=False)
    report={'result':'RUNNING','previous':str(args.previous),
        'verified_previous_gates':[g for g in previous['gates'] if g['result']=='PASS'],
        'previous_integration_passed':10,'gates':[]}
    def save(): (args.output/'report.json').write_text(json.dumps(report,indent=2))
    def execute(name,command,environment=None):
        started=time.monotonic()
        with (args.output/(name+'.log')).open('wb') as output:
            result=subprocess.run(list(map(str,command)),cwd=ROOT,env=environment,
                stdout=output,stderr=subprocess.STDOUT,timeout=1800,
                creationflags=subprocess.CREATE_NO_WINDOW)
        report['gates'].append({'name':name,'returncode':result.returncode,
            'seconds':time.monotonic()-started,'result':'PASS' if result.returncode==0 else 'FAIL'})
        save();print(name,result.returncode,flush=True)
        assert result.returncode==0,name
    try:
        environment=dict(os.environ);environment['CLOTH_NEXT_PPF_EXECUTABLE']=str(args.solver)
        execute('physical-cleanup-integration',[sys.executable,'-m','pytest',
            'tests/integration/test_phase3a_vertical_slice.py','-q'],environment)
        import shutil
        probe_dir=args.output/'sibling-probe';probe_dir.mkdir()
        probe_exe=probe_dir/'ppf-contact-metadata-probe.exe'
        shutil.copyfile(sys.executable,probe_exe)
        probe_env=dict(os.environ);probe_env['PYTHONHOME']=sys.base_prefix
        probe_env['PATH']=sys.base_prefix+os.pathsep+probe_env.get('PATH','')
        probe=subprocess.Popen([str(probe_exe),'-c','import time;time.sleep(600)'],
            env=probe_env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            assert probe.poll() is None
            execute('scoped-sewing-with-live-sibling-probe',[sys.executable,'-m','pytest',
                'tests/integration/test_real_ppf_sewing.py','-q'],environment)
            assert probe.poll() is None,'Sibling probe ended before the gate completed'
            report['sibling_probe']={'pid':probe.pid,'name':probe_exe.name,
                'role':'non-solver process matching the old host-global name scan',
                'all_three_native_solves_passed_while_probe_alive':True}
        finally:
            if probe.poll() is None:probe.terminate()
            probe.wait(timeout=10)
        execute('collider-attachments',[args.blender,'--background','--factory-startup',
            '--disable-autoexec','--python-exit-code','1','--python',
            ROOT/'tools/blender_collider_attachments_smoke.py','--',
            '--solver',args.solver,'--output',args.output/'collider-attachments'])
        execute('idle-keepalive',[sys.executable,ROOT/'tools/probe_gaia_server_idle.py',
            '--solver',args.solver,'--output',args.output/'idle-keepalive'])
        report['result']='PASS'
    except BaseException as error:
        report.update(result='FAIL',error=repr(error));raise
    finally:save()


if __name__=='__main__':main()
