"""Report actual native trajectories and native triangle-force telemetry."""
import argparse
import json
import re
import sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.ppf.schema import envelope,cbor_codec
from tools.report_neon_coupling_investigation import PLAN,OUT,adjacent_faces


def distances(a):
    values=np.linalg.norm(a,axis=-1)
    return dict(max_m=float(values.max()),rms_m=float(np.sqrt(np.mean(values**2))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    a=p.parse_args();report=json.loads((a.input/'report.json').read_text())
    summary=dict(scope='actual native trajectories; force telemetry is native, not a host force reconstruction',
        backend=report.get('backend','cpu'),
        solver_sha256=report['solver_sha256'],quality=report['quality'],cases=[],comparisons={})
    all_positions={}
    for case in report['cases']:
        folder=Path(case.get('artifact_root',a.input/case['label']));session=Path(case['session'])
        positions=np.load(folder/'native-positions.npy',mmap_mode='r')
        mapping=envelope.loads_envelope((folder/'map.cbor').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2)
        triangles=np.fromfile(session/'bin/tri.bin',dtype='<u8').reshape(-1,3)
        owner=np.full(positions.shape[1],-1,dtype=int)
        for i,target in enumerate(PLAN['targets']):owner[mapping[target['uuid']]]=i
        tri_owner=owner[triangles]
        telemetry=[]
        for path in sorted((session/'output').glob('hydro_*.bin'),key=lambda p:int(p.stem.split('_')[1])):
            values=np.fromfile(path,dtype='<f4').reshape(-1,16)
            io_path=path.with_suffix('.io.json')
            clock=json.loads(io_path.read_text()) if io_path.exists() else {}
            assert len(values)==len(triangles) and np.isfinite(values).all()
            frame=int(path.stem.split('_')[1]);loads={}
            for i,target in enumerate(PLAN['targets']):
                mask=(tri_owner==i).all(1);v=values[mask]
                loads[target['name']]=dict(area_m2=float(v[:,0].sum()),wet_area_m2=float(v[:,1].sum()),
                    normal_force_magnitude_sum_n=float(v[:,2].sum()),tangential_force_magnitude_sum_n=float(v[:,3].sum()),
                    resulting_force_n=v[:,4:7].sum(0).tolist(),two_sided_wet_area_m2=float(v[:,11].sum()),
                    mean_fluid_speed_wet_m_s=float(v[:,7].sum()/max(v[:,1].sum(),1e-30)),
                    mean_relative_speed_wet_m_s=float(v[:,8].sum()/max(v[:,1].sum(),1e-30)),
                    max_fluid_speed_m_s=float(v[:,13].max()),max_relative_speed_m_s=float(v[:,14].max()))
            telemetry.append(dict(output_frame=frame,evaluation_clock=clock,objects=loads))
        stats=[cbor_codec.loads(p.read_bytes())['payload'] for p in (session/'output').glob('statistics_[0-9]*.cbor')]
        manifest=cbor_codec.loads((session/'output/statistics_manifest.cbor').read_bytes())['payload']
        ids={o['object_name']:o['object_index'] for o in manifest['objects']}
        result={};all_positions[case['label']]={}
        for target in PLAN['targets']:
            name=target['name'];x=np.asarray(positions[:,mapping[target['uuid']]],float)
            all_positions[case['label']][name]=x
            rest=np.load(OUT/(name+'-mesh.npz'));edges=rest['edges'];tri=rest['triangles']
            lengths=np.linalg.norm(x[:,edges[:,1]]-x[:,edges[:,0]],axis=2)
            valid=lengths[0]>1e-12;ratio=lengths[:,valid]/lengths[0,valid]
            normals=np.cross(x[:,tri[:,1]]-x[:,tri[:,0]],x[:,tri[:,2]]-x[:,tri[:,0]])
            norm=np.linalg.norm(normals,axis=2);normals/=np.maximum(norm[...,None],1e-30)
            pairs=adjacent_faces(tri)
            angles=np.arccos(np.clip(np.sum(normals[:,pairs[:,0]]*normals[:,pairs[:,1]],axis=2),-1,1))
            change=np.abs(angles-angles[0])
            velocity=np.diff(x,axis=0)*30
            result[name]=dict(displacement=distances(x-x[0]),
                velocity_interval_mean_m_s=float(np.linalg.norm(velocity,axis=2).mean()),
                velocity_interval_max_m_s=float(np.linalg.norm(velocity,axis=2).max()),
                edge_stretch_min=float(ratio.min()),edge_stretch_max=float(ratio.max()),
                mean_abs_edge_strain=float(np.mean(np.abs(ratio-1))),
                mean_bend_change_rad=float(change.mean()),max_bend_change_rad=float(change.max()),
                minimum_triangle_area_m2=float(norm.min()/2),
                native_contact_peak=max((o['contact_count'] for f in stats for o in f['objects'] if o['object_index']==ids[name]),default=0),
                finite=bool(np.isfinite(x).all()))
        log=(folder/'native.log').read_text()
        steps=[int(v) for v in re.findall(r'newton_steps: (\d+)',log)]
        residuals=[float(v) for v in re.findall(r'reresid: ([\d.eE+-]+)',log)]
        cgiters=[int(v) for v in re.findall(r'\* iter: (\d+)',log)]
        summary['cases'].append(dict(label=case['label'],seconds=case['seconds'],
            seconds_scope=case.get('seconds_scope','complete case wall time'),
            log_scope=case.get('log_scope','complete native run'),resume=case.get('resume'),
            objects=result,native_loads=telemetry,
            max_newton_steps=max(steps,default=0),max_linear_relative_residual=max(residuals,default=0),
            max_cg_iterations=max(cgiters,default=0),native_advances=len(steps),
            native_finished=(session/'output/finished.txt').exists(),
            native_contact_guard_errors=[s for s in log.splitlines() if s.startswith('###')],
            contact_reaction_vectors_n='not exposed by the current upstream telemetry',
            collider_intersection_check='native CCD/intersection guard; not an independent signed body-SDF audit'))
    for lhs,rhs in (('dry1','flow1'),('dry1','hydro1'),('dry1','dry2'),('hydro1','hydro2')):
        if lhs in all_positions and rhs in all_positions:
            summary['comparisons'][lhs+'__'+rhs]={name:distances(all_positions[rhs][name]-x)
                for name,x in all_positions[lhs].items()}
    (a.input/'numerical-summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(dict(cases=[c['label'] for c in summary['cases']],comparisons=summary['comparisons']),indent=2))


if __name__=='__main__':main()
