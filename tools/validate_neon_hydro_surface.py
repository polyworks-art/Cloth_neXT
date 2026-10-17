"""Quantify native SDF wet-area errors against the source BOBJ at real cloth."""
import argparse
import json
from pathlib import Path
import sys
import tomllib
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def volume_frame(root,k,role):
    m=tomllib.loads((root/'force_field.toml').read_text())['field'][role]['fluid']
    t=np.fromfile(root/m['transform'],dtype='<f8').reshape(-1,15)[k]
    return dict(resolution=[m['resolution'][k]],
        tables=[np.fromfile(root/m['tables'][k],dtype='<u4')],
        bricks=[np.fromfile(root/m['bricks'][k],dtype='<f4').reshape(-1,512,4)],
        corner=[t[:3].tolist()],axes=[t[3:12].reshape(3,3).T.tolist()],
        cell=[t[12:].tolist()],start=0.,step=1.)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--volumes',type=Path,required=True)
    p.add_argument('--dependencies',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.dependencies));import trimesh
    from tools.audit_neon_hydrodynamic_loads import read_surface
    from tools.report_neon_coupling_investigation import load_case,OUT,SAMPLE
    dry=load_case('zone30-bound','dry1');rows=[]
    source=Path('F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache/bakefiles')
    for frame in (1,15,30):
        x,f,sha=read_surface(source/f'{frame:06}.bobj');mesh=trimesh.Trimesh(x,f,process=False)
        volume=volume_frame(a.volumes,frame-1,1)
        for name,poses in dry['points'].items():
            tri=np.load(OUT/(name+'-mesh.npz'))['triangles'];positions=poses[frame-1][tri]
            area=np.linalg.norm(np.cross(positions[:,1]-positions[:,0],positions[:,2]-positions[:,0]),axis=1)/2
            true=np.zeros(len(tri));estimated=np.zeros(len(tri));mismatch=0.
            phi_error=[]
            for b in ((2/3,1/6,1/6),(1/6,2/3,1/6),(1/6,1/6,2/3)):
                p=np.einsum('i,tij->tj',b,positions)
                inside=mesh.contains(p[:,[0,2,1]]*[1,-1,1])
                sdf,_,present=SAMPLE(volume,p,0.)
                wet=(sdf[:,0]<=0)&(np.asarray(present).ravel()>0)
                true+=inside/3;estimated+=wet/3;mismatch+=float(area@(inside!=wet))/3
            rows.append(dict(frame=frame,object=name,true_wet_area_m2=float(area@true),
                sdf_wet_area_m2=float(area@estimated),misclassified_area_m2=mismatch,
                signed_area_error_m2=float(area@(estimated-true))))
            print(rows[-1],flush=True)
    a.output.write_text(json.dumps(dict(scope='three point quadrature on existing dry trajectory',
        volumes=str(a.volumes),comparison='exact closed BOBJ inside test versus native interpolated voxel SDF',
        results=rows),indent=2))


if __name__=='__main__':main()
