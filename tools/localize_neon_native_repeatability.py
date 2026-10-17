"""Separate material-vertex repeatability from sampled geometric surface distance."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);a=p.parse_args();sys.path[:0]=[a.dependencies,str(Path(__file__).resolve().parents[1])]
import numpy as np,trimesh
from cloth_next.ppf.schema import envelope
run=Path('.hydro-test-tmp/blender-e2e-20261016/test-e-130-159');v=json.loads((run/'verification.json').read_text());reference=Path(v['native_output']).parent;mapping=envelope.loads_envelope((reference/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2);scene=next((run/'cache/.cloth_next_export/scene').rglob('scene.cbor'));objects={o['name']:o for part in envelope.loads_envelope(scene.read_bytes(),envelope.KIND_SCENE,schema_version=2) for o in part['object']};ids=np.array(mapping[objects['S04_NEON_Garments']['uuid']],int);tri=np.fromfile(reference/'bin/tri.bin',dtype='<u8').reshape(-1,3);lookup=np.full(max(int(tri.max()),int(ids.max()))+1,-1);lookup[ids]=np.arange(len(ids));faces=lookup[tri];faces=faces[(faces>=0).all(1)]
def positions(session,k):return np.fromfile(session/f'output/vert_{k}.bin',dtype='<f4').reshape(-1,3)[ids].astype(float)
base=[positions(reference,k) for k in range(30)];rows=[]
for label in ('baseline1','baseline2','sdf_plus_2p5mm','sdf_minus_2p5mm'):
 session=Path(f'.hydro-test-tmp/flip-reconstruction-long-sensitivity/{label}/session');best=(-1,None,None,None)
 for k,x in enumerate(base):
  y=positions(session,k);distance=np.linalg.norm(y-x,axis=1);index=int(distance.argmax())
  if distance[index]>best[0]:best=(float(distance[index]),k,index,y)
 maximum,k,index,y=best;x=base[k];delta=y-x;d=np.linalg.norm(delta,axis=1);mx=trimesh.Trimesh(x,faces,process=False);my=trimesh.Trimesh(y,faces,process=False);normal=mx.vertex_normals[index];n=float(abs(delta[index]@normal));distances=[]
 for source,target in ((mx,my),(my,mx)):
  points=np.concatenate([np.asarray(source.vertices),np.asarray(source.triangles).mean(1)]);_,distance,_=trimesh.proximity.closest_point(target,points);distances.append({'samples':len(points),'maximum_m':float(distance.max()),'p99_m':float(np.quantile(distance,.99))})
 row=dict(case=label,object='S04_NEON_Garments',worst_correspondence_frame=130+k,vertex_index=index,world_location_blender_m=(x[index][[0,2,1]]*[1,-1,1]).tolist(),maximum_material_vertex_delta_m=maximum,normal_projection_m=n,tangential_projection_m=float(np.sqrt(max(0,maximum**2-n**2))),vertices_above_8mm_at_worst_frame=int((d>.008).sum()),fraction_above_8mm_at_worst_frame=float((d>.008).mean()),worst_frame_rms_m=float(np.sqrt(np.mean(d**2))),sampled_point_to_triangle=distances,scope='Sampled vertex+triangle-centroid surface distances at worst material-vertex frame only, not Hausdorff, temporal jitter, or an all-frame geometric bound. Original prospective screen remains INVESTIGATE.')
 rows.append(row);Path('docs/validation/gaia026/hydrodynamics/blender-reconstructed-130-159/repeatability-localization.json').write_text(json.dumps(rows,indent=2));print(row,flush=True)
