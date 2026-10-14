# SPDX-License-Identifier: GPL-3.0-or-later
"""Serial real-source Bake/operator/playback gates with dry controls.

Run with the verified bundle Python (numpy/psutil), outside restricted shell
execution, against the installed official candidate. No solver selection or
source file is changed. Every run owns a fresh output directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary,path)


def cache_identity(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return path.stat().st_size,path.stat().st_mtime_ns,digest.hexdigest()


def run_blender(command, directory, timeout):
    started=time.monotonic();peaks={};sampled_tree_peak=0
    with (directory.parent/(directory.name+'.log')).open('wb') as log:
        process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,
            cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        owner=psutil.Process(process.pid)
        known={process.pid:owner}
        while process.poll() is None:
            if time.monotonic()-started>timeout:
                # Blender's probe has its own earlier bounded cancel timeout.
                # Preserve the process and its artifacts for diagnostics.
                raise TimeoutError(f'Blender probe exceeded supervisor timeout: {directory}')
            try:
                for child in owner.children(recursive=True):known.setdefault(child.pid,child)
            except psutil.Error:pass
            rss=0
            for pid, child in tuple(known.items()):
                try:
                    if not child.is_running():
                        known.pop(pid,None)
                        continue
                    memory=child.memory_info();rss+=memory.rss
                    if pid not in peaks:
                        try: name=child.name()
                        except psutil.Error: name='unknown-owned-process'
                        try: argv=child.cmdline()
                        except psutil.Error: argv=[]
                        peaks[pid]={'pid':pid,'name':name,'argv':argv,
                                    'os_peak_working_set_bytes':0}
                    # A process may be sampled while its executable is still
                    # initializing. Retry missing metadata, retaining RAM peaks.
                    if peaks[pid]['name']=='unknown-owned-process':
                        try: peaks[pid]['name']=child.name()
                        except psutil.Error: pass
                    if not peaks[pid]['argv']:
                        try: peaks[pid]['argv']=child.cmdline()
                        except psutil.Error: pass
                    peaks[pid]['os_peak_working_set_bytes']=max(
                        peaks[pid]['os_peak_working_set_bytes'],getattr(memory,'peak_wset',memory.rss))
                except psutil.Error:pass
            sampled_tree_peak=max(sampled_tree_peak,rss)
            time.sleep(.05)
    resources={'returncode':process.returncode,'wall_seconds':time.monotonic()-started,
               'process_peaks':list(peaks.values()),'sampled_tree_peak_rss_bytes':sampled_tree_peak,
               'tree_sample_period_seconds':.05,
               'solver_vram_bytes':None,'vram_reason':'NVIDIA WDDM reports per-process used_memory as N/A'}
    save(directory.parent/(directory.name+'-resources.json'),resources)
    if process.returncode!=0:raise RuntimeError(f'Blender probe failed: {directory}; see its log')
    result=json.loads((directory/'report.json').read_text(encoding='utf-8'))
    if result['result']!='PASS':raise AssertionError(result)
    return result,resources


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--solver',type=Path,required=True)
    parser.add_argument('--blender',type=Path,default=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe'))
    parser.add_argument('--source',type=Path,default=Path(r'F:\16_Cloth_neXt\Flip_Test.blend'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--stages',type=int,nargs='+',default=[3,24,60,120,250])
    parser.add_argument('--gaia',type=Path)
    parser.add_argument('--dry-report',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    report={'result':'RUNNING','stages':[],'entry_point':'existing Blender Bake operator',
            'solver':str(args.solver),'source':str(args.source)}
    if args.gaia and len(args.stages)!=1:
        raise ValueError('An existing .gaia comparison requires one matching stage')
    if args.dry_report and len(args.stages)!=1:
        raise ValueError('A verified dry control requires one matching stage')
    cache_before=cache_identity(args.gaia) if args.gaia else None
    status=args.output/'long-sequence-report.json'
    try:
        for count in args.stages:
            stage={'frames':count,'result':'RUNNING'};report['stages'].append(stage);save(status,report)
            results={}
            for dry in (True,False):
                mode='dry' if dry else 'wet';directory=args.output/f'{count:03d}-{mode}'
                if dry and args.dry_report:
                    result=json.loads(args.dry_report.read_text(encoding='utf-8'))
                    assert result['result']=='PASS' and result['dry'] and result['frames']==count
                    assert Path(result['pc2']).stat().st_size==32+count*4*12
                    results[mode]=result
                    stage[mode]={'report':str(args.dry_report),'reused_verified_control':True}
                    save(status,report)
                    continue
                command=[str(args.blender),'--background','--factory-startup','--disable-autoexec',
                    '--python-exit-code','1','--python',str(ROOT/'tools/blender_gaia_regular_bake_probe.py'),
                    '--','--repo',str(ROOT),'--solver',str(args.solver),'--output',str(directory),
                    '--fluid-source',str(args.source),'--first','1','--last',str(count),'--timeout','1800']
                if dry:command.append('--dry')
                if args.gaia:command+=['--gaia',str(args.gaia)]
                result,resources=run_blender(command,directory,timeout=1900)
                results[mode]=result
                stage[mode]={'report':str(directory/'report.json'),
                             'resources':str(directory.parent/(directory.name+'-resources.json'))}
                save(status,report)
            dry,wet=results['dry'],results['wet']
            assert dry['center']==wet['center'] and dry['frames']==wet['frames']==count
            assert dry['source_blend_unchanged'] and wet['source_blend_unchanged']
            delta=np.asarray(wet['final_local_positions'])-dry['final_local_positions']
            displacement=float(np.linalg.norm(delta,axis=1).max())
            assert np.isfinite(delta).all() and displacement>1e-5, displacement
            metrics=wet['native_file_metrics'];assert len(metrics)==1
            assert metrics[0]['frame_count']==count
            stage.update(result='PASS',max_wet_displacement_vs_dry_m=displacement,
                         wet_wall_seconds=wet['wall_seconds'],dry_wall_seconds=dry['wall_seconds'],
                         prepared_gaia_bytes=wet['prepared_gaia_bytes'],
                         blender_peak_working_set_bytes=wet['blender_peak_working_set_bytes'],
                         native_metrics=metrics[0])
            if args.gaia:
                assert cache_identity(args.gaia)==cache_before
                stage['gaia_reused_without_rewrite']=True
            save(status,report);print(f'REAL_REGULAR_STAGE_PASS {count}',flush=True)
        report['result']='PASS'
    except BaseException as exc:
        report.update(result='FAIL',error=repr(exc))
        raise
    finally:save(status,report)


if __name__=='__main__':main()
