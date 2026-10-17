"""Independent cached-particle support audit of original-mesher replay surfaces."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--dependencies',required=True);a=p.parse_args();sys.path[:0]=[a.dependencies,str(Path(__file__).resolve().parents[1])]
import numpy as np
from scipy.spatial import cKDTree
from tools.audit_neon_hydrodynamic_loads import read_surface
rows=[]
for frame in (130,131,132,133,135):
 case=Path(f'.hydro-test-tmp/flip-grid-edge-reference-{frame}');config=json.loads((case/'input-provenance.json').read_text());x,tri,sha=read_surface(case/'reconstructed.bobj')
 particles=np.memmap(case/'positions.bin',dtype='<f4',mode='r').reshape(-1,3);vel=np.memmap(case/'velocities.bin',dtype='<f4',mode='r').reshape(-1,3);tree=cKDTree(particles)
 radius=config['settings'][3]*(3/(32*np.pi))**(1/3)*config['settings'][6]
 groups={'uniform_surface_vertices':x[np.linspace(0,len(x)-1,min(20000,len(x)),dtype=int)]}
 if frame in (130,135):
  original,_,_=read_surface(Path(f'.hydro-test-tmp/flip-original-reference-{frame}/reconstructed.bobj'));delta,_=cKDTree(original).query(x,workers=4);groups['changed_vertices_above_10um']=x[delta>1e-5]
 report={'frame':frame,'particles':len(particles),'finite_payloads':bool(np.isfinite(particles).all() and np.isfinite(vel).all()),'native_particle_radius_m':radius,'surface_sha256':sha,'groups':{},'transport_approved':False,'limits':'Nearest-particle support is independent of BOBJ validity, but is not the original obstacle-clamped grid field, signed occupancy, or a force acceptance test. Original smoothing shifts the zero surface; do not reinterpret a radius residual as geometric error.'}
 for name,points in groups.items():
  distance,ids=tree.query(points,workers=4);speed=np.linalg.norm(vel[ids],axis=1);residual=distance-radius
  report['groups'][name]={'count':len(points),'nearest_particle_distance_m':{'maximum':float(distance.max()),'p95':float(np.quantile(distance,.95))},'particle_radius_residual_m':{'minimum':float(residual.min()),'maximum':float(residual.max()),'rms':float(np.sqrt(np.mean(residual**2)))},'local_velocity_speed_m_s':{'minimum':float(speed.min()),'maximum':float(speed.max()),'mean':float(speed.mean())}}
 rows.append(report);Path('docs/validation/gaia026/hydrodynamics/flip-original/particle-support.json').write_text(json.dumps(rows,indent=2));print(frame,report['groups'],flush=True)
 del tree,particles,vel
