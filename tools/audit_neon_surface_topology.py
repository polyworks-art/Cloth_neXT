"""Read-only topology audit for the requested mid-sequence FLIP surfaces."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--dependencies',type=Path,required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--first',type=int,default=128)
    p.add_argument('--last',type=int,default=135)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();sys.path.insert(0,str(a.dependencies));sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    import trimesh
    from tools.audit_neon_hydrodynamic_loads import read_surface
    rows=[]
    for frame in range(a.first,a.last+1):
        x,f,sha=read_surface(a.source/f'{frame:06}.bobj')
        mesh=trimesh.Trimesh(vertices=x,faces=f,process=False)
        counts=np.bincount(mesh.edges_unique_inverse);areas=mesh.area_faces
        rec=dict(frame=frame,sha256=sha,vertices=len(x),faces=len(f),watertight=bool(mesh.is_watertight),
            connected_components=int(mesh.body_count),
            winding=bool(mesh.is_winding_consistent),volume=float(mesh.volume),
            boundary_edges=int((counts==1).sum()),nonmanifold_edges=int((counts>2).sum()),
            zero_area_faces=int((areas==0).sum()),minimum_area=float(areas.min()))
        # Exact coincident vertices may be safely indexed together without
        # moving geometry. Report whether that alone closes the source.
        unique,inverse=np.unique(x,axis=0,return_inverse=True)
        merged=trimesh.Trimesh(vertices=unique,faces=inverse[f],process=False)
        rec['exact_merge']=dict(removed_vertices=len(x)-len(unique),watertight=bool(merged.is_watertight),
                               winding=bool(merged.is_winding_consistent))
        rows.append(rec);print(json.dumps(rec),flush=True)
        a.output.write_text(json.dumps(rows,indent=2))


if __name__=='__main__':main()
