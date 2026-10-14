# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the official idle timer and existing TCMD keepalive with a short supported CLI timeout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.ppf.wire import ServerAddress,TransportConfig,send_tcmd
from cloth_next.ppf.layout import BundledSolverLayout


def run(executable,output):
    output.mkdir(parents=True,exist_ok=False)
    before=hashlib.sha256(executable.read_bytes()).hexdigest()
    root=executable.parent.parent.parent.parent
    environment=dict(os.environ)
    environment.update(dict(BundledSolverLayout.from_root(root).process_environment()))
    environment['CARGO_TARGET_DIR']=str(root/'target/cuda')
    cases=[]
    for keepalive in (False,True):
        name='keepalive' if keepalive else 'idle'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        with (output/(name+'.log')).open('wb') as log:
            started=time.monotonic()
            process=subprocess.Popen([str(executable),'--port',str(port),
                '--progress-file',str(output/(name+'.progress')),'--idle-timeout','3'],
                cwd=root,env=environment,
                stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline=started+10
                while True:
                    try:
                        response=send_tcmd(ServerAddress('127.0.0.1',port),
                            TransportConfig(connect_timeout=.2,read_timeout=1.),
                            'clothnext-idle-probe',allow_server_error=True)
                        break
                    except Exception:
                        if process.poll() is not None or time.monotonic()>deadline:raise
                        time.sleep(.1)
                requests=1
                assert str(response['protocol_version'])=='0.26',response
                ready=time.monotonic()
                if keepalive:
                    while time.monotonic()-ready<8:
                        time.sleep(.4)
                        assert process.poll() is None,'Keepalive did not preserve the server'
                        send_tcmd(ServerAddress('127.0.0.1',port),
                            TransportConfig(connect_timeout=1.,read_timeout=1.),
                            'clothnext-idle-probe',allow_server_error=True)
                        requests+=1
                stopped=time.monotonic()
                process.wait(timeout=8)
                idle_seconds=time.monotonic()-stopped
                assert 2.5<=idle_seconds<=8.,idle_seconds
                assert process.returncode==0,process.returncode
                cases.append({'case':name,'result':'PASS','requests':requests,
                    'connected_seconds':stopped-ready,'idle_exit_seconds':idle_seconds})
            finally:
                if process.poll() is None:
                    process.terminate();process.wait(timeout=10)
    from cloth_next.ppf.process import SolverProcessConfig
    from tools.run_ppf_vertical_slice import run as vertical_slice
    original_arguments=SolverProcessConfig.arguments
    def bounded_arguments(config):
        return original_arguments(config)+['--idle-timeout','3']
    SolverProcessConfig.arguments=bounded_arguments
    try:
        busy=vertical_slice(executable,output/'active-session',frame_count=8)
        assert busy['result']=='PASS' and busy['solver_frames_fetched']==list(range(1,8))
        cases.append({'case':'active-session-with-production-polling','result':'PASS',
                      'frames':8,'report':str(output/'active-session')})
    finally:
        SolverProcessConfig.arguments=original_arguments
    report={'result':'PASS','timeout_cli_seconds':3,'production_default_seconds':3600,
        'cases':cases,'binary_unchanged':before==hashlib.sha256(executable.read_bytes()).hexdigest(),
        'scope':'idle expiry, existing TCMD keepalive, actual Build/Solve/Fetch with production polling; no independent busy-without-polling claim'}
    assert report['binary_unchanged']
    (output/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solver',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.solver.resolve(),args.output.resolve())
