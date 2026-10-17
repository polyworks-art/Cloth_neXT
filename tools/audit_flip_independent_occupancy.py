"""Independent original single-chunk control: occupancy near real cloth."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);p.add_argument('--frames',type=int,nargs='+',default=[130,135]);p.add_argument('--output',type=Path,default=Path('docs/validation/gaia026/hydrodynamics/flip-original/independent-occupancy-control.json'));a=p.parse_args();sys.path[:0]=[a.dependencies,str(Path(__file__).resolve().parents[1])]
import numpy as np,trimesh
from cloth_next.ppf.schema import envelope
from tools.audit_neon_hydrodynamic_loads import read_surface
run=Path('.hydro-test-tmp/blender-e2e-20261016/test-a-131-133-r9');v=json.loads((run/'verification.json').read_text());source=Path(v['native_output']).parent
scene=next((run/'cache/.cloth_next_export/scene').rglob('scene.cbor'));objects={o['name']:o for part in envelope.loads_envelope(scene.read_bytes(),envelope.KIND_SCENE,schema_version=2) for o in part['object']};mapping=envelope.loads_envelope((source/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2);tri=np.fromfile(source/'bin/tri.bin',dtype='<u8').reshape(-1,3)
pose=np.fromfile(source/'output/vert_0.bin',dtype='<f4').reshape(-1,3).astype(float);names=[n for n in objects if n.startswith('S04_NEON_Garments')];owner=np.full(len(pose),-1)
for i,n in enumerate(names):owner[mapping[objects[n]['uuid']]]=i
cloth=pose[owner>=0][:,[0,2,1]]*[1,-1,1];lo=cloth.min(0)-.025;hi=cloth.max(0)+.025;random=np.random.default_rng(20261016).uniform(lo,hi,(100000,3));rows=[]
for frame in a.frames:
 meshes=[]
 for folder in (f'flip-grid-edge-reference-{frame}',f'flip-independent-singlechunk-{frame}'):
  x,f,_=read_surface(Path('.hydro-test-tmp')/folder/'reconstructed.bobj');m=trimesh.Trimesh(x,f,process=False);assert m.is_watertight and m.is_winding_consistent;meshes.append(m)
 inside=[m.contains(random) for m in meshes];row={'frame':frame,'control':'Original FLIP mesher, one compute chunk, unchanged particles/grid/radius/static obstacle/smoothing; no cross-chunk join. Control only, not selected transport.', 'frozen_cloth_geometry':'Preserved native frame131 initial state; not an assertion of actual 130/135 trajectories.', 'near_cloth_uniform_samples':100000,'near_cloth_sample_bounds_m':[lo.tolist(),hi.tolist()],'near_cloth_occupancy_disagreements':int(np.count_nonzero(inside[0]!=inside[1])),'wet_area':[],'transport_approved':False}
 for i,n in enumerate(names):
  t=tri[(owner[tri]==i).all(1)];vertices=pose[t][:,:,[0,2,1]]*[1,-1,1];area=np.linalg.norm(np.cross(vertices[:,1]-vertices[:,0],vertices[:,2]-vertices[:,0]),axis=1)/2;wet=np.zeros(2);mismatch=0.
  for b in ((2/3,1/6,1/6),(1/6,2/3,1/6),(1/6,1/6,2/3)):
   points=np.einsum('i,tij->tj',b,vertices);ins=[m.contains(points) for m in meshes];wet+=[float(area@z)/3 for z in ins];mismatch+=float(area@(ins[0]!=ins[1]))/3
  row['wet_area'].append(dict(object=n,candidate_m2=float(wet[0]),independent_control_m2=float(wet[1]),misclassified_area_m2=mismatch,screen='PASS' if mismatch<=max(.01*float(area.sum()),.05*wet[1]) else 'INVESTIGATE'))
 rows.append(row);a.output.write_text(json.dumps(rows,indent=2));print(row,flush=True)
