# SPDX-License-Identifier: GPL-3.0-or-later
"""Real production SolverSession/PC2 streaming harness; no source scene changes."""
import argparse
from pathlib import Path
import sys
import json
import threading
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools.run_ppf_vertical_slice import run

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solver',type=Path,required=True)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--first',type=int,default=1)
    parser.add_argument('--frames',type=int,default=250)
    parser.add_argument('--inspect-build',action='store_true')
    args=parser.parse_args()
    if args.inspect_build:
        import os
        from cloth_next.ppf_run.session import SolverSession
        original=SolverSession._await_build
        def inspect(self):
            try:original(self)
            except BaseException:
                root=self._server_data_root()/self.scene.project_name
                print('FAILED BUILD FILES',str(root),list(root.rglob('*')),flush=True)
                from cloth_next.ppf.project_links import canonical_project_path
                alias=canonical_project_path(args.solver,self.scene.project_name)
                print('ALIAS',str(alias),os.path.lexists(alias),alias.is_dir(),
                      os.readlink(alias) if os.path.lexists(alias) else '',flush=True)
                print('ALIAS CASH',os.path.lexists(alias/'.cash'),(alias/'.cash').is_dir(),flush=True)
                for path in root.rglob('.cash'):
                    print('CACHE DIRECTORY',str(path),path.is_dir(),path.is_symlink(),
                          os.readlink(path) if path.is_symlink() else '',flush=True)
                raise
        SolverSession._await_build=inspect
    import psutil
    peaks={};stop=threading.Event();parent=psutil.Process()
    def sample():
        while not stop.is_set():
            for process in [parent,*parent.children(recursive=True)]:
                try:
                    key='parent' if process.pid==parent.pid else process.name()
                    peaks[key]=max(peaks.get(key,0),process.memory_info().rss)
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            stop.wait(.05)
    sampler=threading.Thread(target=sample,daemon=True);sampler.start()
    started=time.perf_counter()
    try:
        report=run(args.solver,args.output,frame_count=args.frames,contact_enabled=False,
            water_path=args.cache,water_first=args.first)
    finally:
        stop.set();sampler.join(timeout=2.)
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/'stream-memory.json').write_text(json.dumps({
            'peak_rss_bytes':peaks,'seconds':time.perf_counter()-started,
            'frames':args.frames},indent=2))
    assert report['result']=='PASS'

if __name__=='__main__':main()
