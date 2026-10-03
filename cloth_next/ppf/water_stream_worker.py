# SPDX-License-Identifier: GPL-3.0-or-later
"""CNX-owned held-stream worker run by the selected official Python runtime."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sys
import time
import zlib
import subprocess

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
if not __package__:
    __package__=Path(__file__).resolve().parents[1].name+'.ppf'
from ..gaia.streaming import WaterStreamReader,MAX_STREAM_VALUES_BYTES
from ..gaia.held_stream import StreamTarget,drive_held_stream,WaterStreamCancelled
from .schema import envelope


def atomic_status(path, state):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,allow_nan=False),encoding='utf-8')
    os.replace(temporary,path)


def run(args):
    # Do not import/start the official frontend before native ownership exists.
    deadline=time.monotonic()+30.
    while not args.startup_gate.is_file():
        if time.monotonic()>deadline:raise RuntimeError('Owned helper startup gate timed out')
        time.sleep(.02)
    from frontend import App
    from frontend._force_field_ import ForceField
    import numpy as np
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.project_name):
        raise ValueError('Unsafe Water Flow project identity')
    if os.environ.get('PPF_CTS_DATA_ROOT'):
        raise ValueError('Official Water Flow must not use a solver test-rig override')
    directory=args.project_root.resolve(strict=True)/'session'
    if not (directory/'fixed_session.pickle').is_file():
        raise ValueError('Official saved scene is missing')
    document=json.loads(args.descriptor.read_text(encoding='utf-8'))
    if document['version']!=1:raise ValueError('Unsupported Water Flow descriptor')
    tree=envelope.loads_envelope(args.params.read_bytes(),envelope.KIND_PARAM,schema_version=2)
    base=tree.get('force_field',{})
    def labels(groups):
        return None if groups is None else [f'addon-group-{int(i)}' for i in groups]
    def add_base(field):
        for grid in base.get('grids',[]):
            values=np.frombuffer(zlib.decompress(grid['data']),dtype='<f4').reshape(grid['shape'])
            field.grid(values,grid['min'],grid['max'],times=grid['times'],
                       kind=grid['kind'],groups=labels(grid.get('groups')))
        for script in base.get('scripts',[]):
            field.script(script['source'],z_up=True,groups=labels(script.get('groups')))
    pointer=Path(App.get_data_dirpath())/'symlinks'/(args.project_name+'.txt')
    pointer.parent.mkdir(parents=True,exist_ok=True)
    target=str(directory);created=False
    state={'phase':'STARTING','boundary':args.resume_boundary,
           'metrics':{'windows':0,'read_seconds':0.,'decode_validate_seconds':0.,
                      'encode_seconds':0.,'upload_seconds':0.,'step_seconds':0.}}
    native=None
    try:
        try:
            with pointer.open('x',encoding='utf-8') as stream:stream.write(target)
            created=True
        except FileExistsError:
            if pointer.read_text(encoding='utf-8').strip()!=target:
                raise ValueError('Official project pointer belongs to another session')
        session=App.recover(args.project_name)
        if Path(session.info.path).resolve()!=directory:
            raise ValueError('Official recovery selected a different project')
        def start_resume(boundary,timeout):
            nonlocal native
            if boundary not in session.get.saved():
                raise ValueError('The authenticated Water Flow checkpoint is missing')
            output=Path(session.output.path).resolve()
            if output!=directory/'output':raise ValueError('Unexpected official output ownership')
            quit_flag=Path(session.save_and_quit_file_path()).resolve()
            if quit_flag.parent!=output:raise ValueError('Unexpected official checkpoint flag ownership')
            for stale in (quit_flag,output/'held',output/'inputs_updated'):
                stale.unlink(missing_ok=True)
            # Public native CLI + documented held-file protocol. GAIA 0.23's
            # automatic start -> resume -> start clears the pending hold on
            # its second start. Installing it before direct --load avoids any
            # integration with a stale field, without changing frontend code.
            hold=output/'hold_at_frame';temporary=hold.with_suffix('.cnx-tmp')
            temporary.write_text(f'{boundary} {timeout}\n',encoding='ascii')
            os.replace(temporary,hold)
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            with (directory/'stdout.log').open('ab') as stdout, (directory/'stderr.log').open('ab') as stderr:
                native=subprocess.Popen([str(args.native_worker),'--path',str(directory),
                    '--output',str(output),'--load',str(boundary)],cwd=Path.cwd(),
                    stdout=stdout,stderr=stderr,stdin=subprocess.DEVNULL,shell=False,**options)
            deadline=time.monotonic()+timeout
            while not session.is_running():
                if native.poll() is not None:
                    raise RuntimeError(f'Official Water Flow resume exited with code {native.returncode}')
                if time.monotonic()>=deadline:raise TimeoutError('Official Water Flow resume did not start')
                time.sleep(.02)
        def notify(event):
            state.update(event)
            if event['phase']=='ACTIVE':
                state['active_windows']=event['targets']
                state['metrics']['windows']+=1
                state['metrics']['upload_seconds']+=event['upload_seconds']
                for target in event['targets']:
                    for name,value in target.get('timings',{}).items():state['metrics'][name]+=value
            if event['phase']=='COMPLETED':
                state['last_completed_boundary']=event['boundary']
                state['metrics']['step_seconds']+=event['step_seconds']
            state['next_boundary_source_frame']=min(document['last'],
                                                    document['first']+state['boundary']+1)
            atomic_status(args.status,state)
        with ExitStack() as stack:
            targets=[]
            for item in document['targets']:
                reader=stack.enter_context(WaterStreamReader(item['path'],
                    first=document['first'],last=document['last'],fps=document['fps'],
                    expected_fingerprint=item['fingerprint']))
                targets.append(StreamTarget(reader,tuple(labels(item['groups'])),
                                           item['influence'],item['velocity_scale']))
            active_bytes=sum(target.reader.frame_bytes*min(2,document['last']-document['first']+1)
                             for target in targets)
            active_bytes+=sum(int(np.prod(grid['shape']))*4 for grid in base.get('grids',[]))
            if active_bytes>MAX_STREAM_VALUES_BYTES:
                raise ValueError('Combined Water Flow windows exceed the 128 MiB active values budget')
            drive_held_stream(session,targets,ForceField,
                final_boundary=document['last']-document['first'],
                resume_boundary=args.resume_boundary,
                cancelled=args.cancel.is_file,notify=notify,add_base_fields=add_base,
                start_held=start_resume if args.resume_boundary else None)
        if native is not None and native.wait(timeout=30.)!=0:
            raise RuntimeError(f'Official Water Flow resume exited with code {native.returncode}')
        state['phase']='FINISHED';atomic_status(args.status,state)
    except WaterStreamCancelled:
        state['phase']='CANCELLED';atomic_status(args.status,state)
    except BaseException as exc:
        state.update(phase='FAILED',error=f'{type(exc).__name__}: {exc}')
        atomic_status(args.status,state)
        raise
    finally:
        if created and pointer.is_file() and pointer.read_text(encoding='utf-8').strip()==target:
            pointer.unlink()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-name',required=True)
    for option in ('project-root','descriptor','params','status','cancel','startup-gate'):
        parser.add_argument('--'+option,required=True,type=Path)
    parser.add_argument('--resume-boundary',type=int,default=0)
    parser.add_argument('--native-worker',required=True,type=Path)
    run(parser.parse_args())


if __name__=='__main__':main()
