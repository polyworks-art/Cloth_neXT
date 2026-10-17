"""Independent, reciprocal cloth/collider edge-triangle crossing audit.

This checks exported surfaces. Open collider meshes do not support an unqualified
inside/outside test, and this does not replace continuous collision detection.
"""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.ppf.schema import envelope


def unique_edges(faces):
    return np.unique(np.sort(faces[:,[0,1,1,2,2,0]].reshape(-1,2),axis=1),axis=0)


def count_crossings(mesh,vertices,edges,tolerance=1e-6):
    count=0;examples=[]
    for begin in range(0,len(edges),16384):
        pair=edges[begin:begin+16384];start=vertices[pair[:,0]];delta=vertices[pair[:,1]]-start
        length=np.linalg.norm(delta,axis=1);valid=length>2*tolerance
        start=start[valid];direction=delta[valid]/length[valid,None];length=length[valid]
        locations,ray,_=mesh.ray.intersects_location(start+tolerance*direction,direction,multiple_hits=False)
        if not len(ray):continue
        distance=np.einsum('ij,ij->i',locations-start[ray],direction[ray])
        hit=(distance>tolerance)&(distance<length[ray]-tolerance)
        count+=int(hit.sum())
        for point in locations[hit][:max(0,5-len(examples))]:examples.append(point.tolist())
    return dict(crossing_segments=count,examples_solver_m=examples)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--python-deps',type=Path,required=True)
    p.add_argument('--frames',type=int,nargs='+')
    a=p.parse_args();sys.path.insert(0,str(a.python_deps));import trimesh
    if not trimesh.ray.has_embree:raise RuntimeError('Embree required')
    # Known interior hit, miss, too-short segment, and excluded endpoint hit.
    plane=trimesh.Trimesh([[0,-1,-1],[0,1,-1],[0,0,1]],[[0,1,2]],process=False)
    points=np.array([[-1,0,0],[1,0,0],[-1,2,2],[1,2,2],[-.2,0,0],[0,0,0]],float)
    assert count_crossings(plane,points,np.array([[0,1],[2,3],[0,4],[0,5]]))['crossing_segments']==1
    report=json.loads((a.input/'report.json').read_text())
    scene=envelope.loads_envelope(Path(report['source_scene']).read_bytes(),envelope.KIND_SCENE,schema_version=2)
    objects=[o for part in scene for o in part['object']]
    records=[]
    for case in report['cases']:
        folder=Path(case.get('artifact_root',a.input/case['label']))
        x=np.load(folder/'native-positions.npy',mmap_mode='r')
        mapping=envelope.loads_envelope((folder/'map.cbor').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2)
        tri=np.fromfile(Path(case['session'])/'bin/tri.bin',dtype='<u8').reshape(-1,3)
        cloth=np.concatenate([mapping[o['uuid']] for o in objects if o['name'].startswith('S04_NEON_Garments')])
        body=np.concatenate([mapping[o['uuid']] for o in objects if o['name'].startswith('SW_04_')])
        ci=np.full(x.shape[1],-1);ci[cloth]=np.arange(len(cloth))
        bi=np.full(x.shape[1],-1);bi[body]=np.arange(len(body))
        cf=ci[tri];bf=bi[tri];cm=(cf>=0).all(1);bm=(bf>=0).all(1)
        if not (cm|bm).all():raise ValueError('Unclassified native faces')
        cf=cf[cm];bf=bf[bm];ce=unique_edges(cf);be=unique_edges(bf)
        cached_body=None;body_mesh=None
        for frame in (a.frames if a.frames is not None else range(len(x))):
            cv=np.asarray(x[frame,cloth],float);bv=np.asarray(x[frame,body],float)
            if cached_body is None or not np.allclose(cached_body,bv,atol=1e-8,rtol=0):
                body_mesh=trimesh.Trimesh(bv,bf,process=False);cached_body=bv.copy()
            cloth_mesh=trimesh.Trimesh(cv,cf,process=False)
            one=count_crossings(body_mesh,cv,ce);two=count_crossings(cloth_mesh,bv,be)
            record=dict(case=case['label'],frame=frame,cloth_edges_vs_collider=one,
                        collider_edges_vs_cloth=two,collider_watertight=bool(body_mesh.is_watertight))
            records.append(record);print(record,flush=True)
        result=dict(analytic_check='PASS',segment_endpoint_exclusion_m=1e-6,cases=records,
                    scope='Reciprocal edge-triangle intersections at exported frames; no global inside/outside or between-frame claim',
                    crossing_segments=sum(v[d]['crossing_segments'] for v in records for d in ('cloth_edges_vs_collider','collider_edges_vs_cloth')))
        (a.input/'collider-crossing-audit.json').write_text(json.dumps(result,indent=2))


if __name__=='__main__':main()
