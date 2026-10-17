"""Frozen native cloth quadrature: exact BOBJ wetting versus SDF sensitivity."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);a=p.parse_args();sys.path[:0]=[a.dependencies,str(Path(__file__).resolve().parents[1])]
import numpy as np,trimesh
from cloth_next.ppf.schema import envelope
from tools.audit_neon_hydrodynamic_loads import read_surface
from tools.validate_neon_hydro_surface import volume_frame
from tools.report_neon_coupling_investigation import SAMPLE
run=Path('.hydro-test-tmp/blender-e2e-20261016/test-a-131-133-r9');v=json.loads((run/'verification.json').read_text());source=Path(v['native_output']).parent
scene=next((run/'cache/.cloth_next_export/scene').rglob('scene.cbor'));objects={o['name']:o for part in envelope.loads_envelope(scene.read_bytes(),envelope.KIND_SCENE,schema_version=2) for o in part['object']};mapping=envelope.loads_envelope((source/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2)
tri=np.fromfile(source/'bin/tri.bin',dtype='<u8').reshape(-1,3);owner=np.full(max(int(tri.max())+1,max(max(ids) for ids in mapping.values())+1),-1)
names=[n for n in objects if n.startswith('S04_NEON_Garments')]
for i,name in enumerate(names):owner[mapping[objects[name]['uuid']]]=i
roots={'fine7p826mm':Path('.hydro-test-tmp/flip-grid-edge-transport-131-134'),'coarse15p653mm':Path('.hydro-test-tmp/flip-grid-edge-transport-131-134-scale2')};rows=[]
for k,frame in enumerate((131,132,133)):
 pose=np.fromfile(source/f'output/vert_{k}.bin',dtype='<f4').reshape(-1,3).astype(float);x,f,_=read_surface(Path(f'F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache/bakefiles/{frame:06}.bobj'));mesh=trimesh.Trimesh(x,f,process=False)
 volumes={n:volume_frame(r,k,1) for n,r in roots.items()}
 for i,name in enumerate(names):
  t=tri[(owner[tri]==i).all(1)];vertices=pose[t];area=np.linalg.norm(np.cross(vertices[:,1]-vertices[:,0],vertices[:,2]-vertices[:,0]),axis=1)/2;true=0.;measure={n:dict(wet_area_m2=0.,misclassified_area_m2=0.,missing_area_m2=0.) for n in roots}
  for b in ((2/3,1/6,1/6),(1/6,2/3,1/6),(1/6,1/6,2/3)):
   points=np.einsum('i,tij->tj',b,vertices);inside=mesh.contains(points[:,[0,2,1]]*[1,-1,1]);true+=float(area@inside)/3
   for n,volume in volumes.items():
    values,_,presence=SAMPLE(volume,points,0.);support=np.asarray(presence).ravel()>0;wet=values[:,0]<=0
    measure[n]['wet_area_m2']+=float(area@(wet&support))/3;measure[n]['misclassified_area_m2']+=float(area@(inside!=wet))/3;measure[n]['missing_area_m2']+=float(area@~support)/3
  total=float(area.sum());limit=max(.01*total,.05*true)
  for n,r in measure.items():r.update(signed_area_error_m2=r['wet_area_m2']-true,target_misclassification_limit_m2=limit,occupancy_screen='PASS' if r['misclassified_area_m2']<=limit and r['missing_area_m2']==0 else 'INVESTIGATE')
  row=dict(frame=frame,object=name,total_area_m2=total,exact_reference_wet_area_m2=true,measurements=measure);rows.append(row);print(row,flush=True)
  Path('docs/validation/gaia026/hydrodynamics/flip-original/frozen-wet-area-sensitivity.json').write_text(json.dumps({'scope':'Three-point quadrature on preserved native reference output states, matching source frames; no new force implementation.','criteria':'docs/GAIA_PRACTICAL_RECONSTRUCTION_ACCEPTANCE.md','rows':rows},indent=2))
