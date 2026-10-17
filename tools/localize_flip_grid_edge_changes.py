"""Localize changed seam geometry; not an acceptance test or a Hausdorff bound."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);a=p.parse_args();sys.path[:0]=[a.dependencies,str(Path(__file__).resolve().parents[1])]
import numpy as np
from scipy.spatial import cKDTree
from tools.audit_neon_hydrodynamic_loads import read_surface
rows=[]
for frame in (130,135):
 root=Path('.hydro-test-tmp');x,f,_=read_surface(root/f'flip-original-reference-{frame}/reconstructed.bobj');y,g,_=read_surface(root/f'flip-grid-edge-reference-{frame}/reconstructed.bobj')
 distance,mapping=cKDTree(x).query(y,workers=4);assert len(np.unique(mapping))==len(x)
 changed=distance>1e-5; points=y[changed]
 one=np.sort(f,axis=1);two=np.sort(mapping[g],axis=1)
 changed_faces=np.any(one!=two,axis=1)
 rows.append(dict(frame=frame,nearest_map_is_bijective=True,vertices_above_10um=int(changed.sum()),changed_vertex_bounds_m=[points.min(0).tolist(),points.max(0).tolist()],changed_triangle_rows=int(changed_faces.sum()),nearest_vertex_distance_max_m=float(distance.max()),note='Nearest-vertex correspondence/localization only, not triangle Hausdorff or independent liquid validity; malformed source cannot be ground truth.'))
Path('docs/validation/gaia026/hydrodynamics/flip-original/seam-localization.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows,indent=2))
