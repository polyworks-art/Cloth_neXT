"""Serial production-scale validation; explicit bounded gates, no activation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_gaia026_long_sequence import run_blender, save, cache_identity

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--gaia',type=Path,help='Optional immutable reference cache; stages prepare their own .gaia')
    p.add_argument('--source',type=Path,default=Path(r'F:\16_Cloth_neXt\Flip_Test.blend'))
    p.add_argument('--pilot',action='store_true')
    p.add_argument('--case-timeout',type=int,default=1800)
    p.add_argument('--long-timeout',type=int,default=5400)
    p.add_argument('--sizes',type=int,nargs='+',default=[1000,5000,20000,50000])
    p.add_argument('--frames',type=int,nargs='+',default=[3,60,250])
    p.add_argument('--field-resolution',type=int,default=100)
    p.add_argument('--existing-scene',action='store_true')
    p.add_argument('--exclude-collider',action='append',default=[])
    p.add_argument('--combinations',nargs='+',default=['base','collider','zones','two','folded'])
    p.add_argument('--geometry-only',action='store_true')
    p.add_argument('--time-step',type=float)
    p.add_argument('--cg-max-iter',type=int)
    p.add_argument('--cg-tol',type=float)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    source=a.source
    blender=Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe')
    before=cache_identity(a.gaia) if a.gaia else None
    report=dict(result='RUNNING',cases=[],unexecuted=[],case_timeout_seconds=a.case_timeout,
                reference_gaia=str(a.gaia) if a.gaia else None,source=str(source),payload_limit_bytes=1073741824,long_timeout_seconds=a.long_timeout)
    def write():save(a.output/'report.json',report)
    def run(vertices,frames,combination='base',strength=.2,dry=False,rebake=False):
        label=f'{"scene" if a.existing_scene else vertices}-{frames}-{combination}-{strength:g}-'+('dry' if dry else 'wet')
        directory=a.output/label
        record=dict(label=label,vertices_per_cloth=vertices,frames=frames,combination=combination,
                    strength=strength,dry=dry,result='RUNNING')
        report['cases'].append(record);write()
        timeout=a.long_timeout if frames>=200 else a.case_timeout
        command=[str(blender),'--background','--factory-startup','--disable-autoexec',
            '--python-exit-code','1','--python',str(ROOT/'tools/blender_gaia026_production_scale.py'),
            '--','--repo',str(ROOT),'--solver',str(a.solver),'--output',str(directory),
            '--fluid-source',str(source),'--last',str(frames),'--timeout',str(timeout),
            '--vertices',str(vertices),'--combination',combination,'--strength',str(strength),
            '--gaia',str(a.output/'fields'/f'{frames}.gaia'),
            '--field-resolution',str(a.field_resolution)]
        if dry:command.append('--dry')
        for name in a.exclude_collider:command += ['--exclude-collider',name]
        if a.existing_scene:
            command[command.index('--disable-autoexec')]='--enable-autoexec'
            command.append('--existing-scene')
        if rebake:command.append('--rebake')
        for option,value in [('time-step',a.time_step),('cg-max-iter',a.cg_max_iter),('cg-tol',a.cg_tol)]:
            if value is not None:command += ['--'+option,str(value)]
        try:
            result,resources=run_blender(command,directory,timeout+180)
            record.update(result='PASS',wall_seconds=result['wall_seconds'],
                report=str(directory/'report.json'),resources=str(directory.parent/(directory.name+'-resources.json')))
            metrics=result['native_file_metrics']
            record['native_metrics']=metrics
            record['objects']=result['objects']
            if a.existing_scene:
                record['vertices_per_cloth']=[o['vertices'] for o in result['objects']]
                record['total_cloth_vertices']=result['total_cloth_vertices']
            record['solver_diagnostics_samples']=result['solver_diagnostics_samples']
            assert result['source_blend_unchanged'] and result['source_cache_unchanged']
        except Exception as exc:
            record.update(result='FAIL',error=repr(exc),report=str(directory/'report.json'))
        if a.gaia:assert cache_identity(a.gaia)==before,'Shared .gaia source changed'
        write();print(label,record['result'],flush=True)
        return record
    try:
        sizes=[1000] if a.pilot else a.sizes
        for vertices in sizes:
            previous=run(vertices,3 if a.pilot else a.frames[0])
            for frames in ([] if a.pilot else a.frames[1:]):
                if previous['result']!='PASS':
                    report['unexecuted'].append(dict(vertices=vertices,frames=frames,
                        reason='Previous smaller sequence failed'));write();continue
                previous=run(vertices,frames)
        if not a.pilot and not a.geometry_only:
            for combination,strength in [('base',.05),('base',1.),('collider',.2),
                                         ('zones',.2),('two',.2),('folded',.2),('sweep',.2)]:
                if combination not in a.combinations:continue
                previous=run(1000,12,combination,strength,rebake=combination=='base' and strength==.05)
                for frames in [60,250]:
                    if previous['result']=='PASS':
                        previous=run(1000,frames,combination,strength)
                    else:
                        report['unexecuted'].append(dict(vertices=1000,frames=frames,
                            combination=combination,strength=strength,reason='Failed prior gate or explicit time budget'));write()
            for vertices in [1000,5000]:
                wet=next((r for r in report['cases'] if r['vertices_per_cloth']==vertices and
                          r['frames']==60 and r['combination']=='base' and r['strength']==.2 and r['result']=='PASS'),None)
                if wet:
                    dry=run(vertices,60,dry=True)
                    if dry['result']=='PASS':
                        # Compare real PC2 coordinates, never apply host-side forces.
                        from cloth_next.bake.pc2 import iter_frames
                        x=np.asarray(list(iter_frames(Path(wet['objects'][0]['pc2']))))
                        y=np.asarray(list(iter_frames(Path(dry['objects'][0]['pc2']))))
                        dry['max_wet_dry_displacement_m']=float(np.linalg.norm(x-y,axis=2).max())
                        write()
        report['result']='PASS' if all(c['result']=='PASS' for c in report['cases']) else 'FAIL'
    finally:
        report['reference_gaia_unchanged']=cache_identity(a.gaia)==before if a.gaia else None
        write()

if __name__=='__main__':main()
