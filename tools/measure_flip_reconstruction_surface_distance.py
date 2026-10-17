"""Measure sampled point-to-triangle distance, explicitly not Hausdorff."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);a=p.parse_args();sys.path[:0]=[a.dependencies,str(Path(__file__).resolve().parents[1])]
import numpy as np,trimesh
from scipy.spatial import cKDTree
from tools.audit_neon_hydrodynamic_loads import read_surface
rows=[]
for frame in (130,135):
 x,f,_=read_surface(Path(f'.hydro-test-tmp/flip-original-reference-{frame}/reconstructed.bobj'));y,g,_=read_surface(Path(f'.hydro-test-tmp/flip-grid-edge-reference-{frame}/reconstructed.bobj'))
 d,ids=cKDTree(x).query(y,workers=4);changed=y[d>1e-5];lo=changed.min(0)-.03;hi=changed.max(0)+.03
 local=[]
 for vertices,faces in ((x,f),(y,g)):
  t=vertices[faces];keep=(t.max(1)>=lo).all(1)&(t.min(1)<=hi).all(1);local.append(trimesh.Trimesh(vertices,faces[keep],process=False))
 values=[]
 for source,target in ((local[0],local[1]),(local[1],local[0])):
  triangles=np.asarray(source.triangles);samples=[triangles.mean(1),*[(triangles@np.eye(3))[:,i] for i in range(3)]]
  for b in ((.5,.5,0),(.5,0,.5),(0,.5,.5),(2/3,1/6,1/6),(1/6,2/3,1/6),(1/6,1/6,2/3)):samples.append(np.einsum('i,tij->tj',b,triangles))
  samples=np.concatenate(samples);_,distance,_=trimesh.proximity.closest_point(target,samples)
  values.append({'sample_count':len(samples),'maximum_m':float(distance.max()),'rms_m':float(np.sqrt(np.mean(distance**2))),'p99_m':float(np.quantile(distance,.99))})
 rows.append({'frame':frame,'metric':'Bidirectional sampled point-to-triangle distances in 30mm-expanded changed region; 10 barycentric/vertex samples per triangle. Not a certified Hausdorff bound; source is defective.','region_bounds_m':[lo.tolist(),hi.tolist()],'local_triangle_counts':[len(m.faces) for m in local],'reference_to_candidate':values[0],'candidate_to_reference':values[1],'maximum_observed_m':max(v['maximum_m'] for v in values)})
 Path('docs/validation/gaia026/hydrodynamics/flip-original/point-triangle-distance.json').write_text(json.dumps(rows,indent=2));print(rows[-1],flush=True)
