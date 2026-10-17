"""Native telemetry consistency and temporal screening; no host force model."""
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from cloth_next.core.filesystem_paths import io_path
r=json.loads((a.run/'verification.json').read_text());assert r['result']=='PASS';native=io_path(Path(r['native_output']),reserved_length=1000);rows=[]
for f in sorted(native.glob('hydro_*.bin'),key=lambda x:int(x.stem.split('_')[1])):
 v=np.fromfile(f,dtype='<f4').reshape(-1,16).astype(float);assert np.isfinite(v).all();assert (v[:,:4]>=0).all();assert (v[:,1]<=v[:,0]+1e-7).all();assert (v[:,3]==0).all()
 # Cn=1 drag: projection cannot exceed the full relative speed. A bound,
 # not a second force implementation or a force-equivalence assertion.
 bound=.5*1000*v[:,1]*v[:,14]**2;excess=v[:,2]-bound;assert (excess<=1e-5+1e-4*bound).all()
 clock=json.loads(f.with_suffix('.io.json').read_text());wet=float(v[:,1].sum());row=dict(output_frame=int(f.stem.split('_')[1]),clock=clock,area_m2=float(v[:,0].sum()),wet_area_m2=wet,normal_load_n=float(v[:,2].sum()),net_force_n=v[:,4:7].sum(0).tolist(),maximum_relative_speed_m_s=float(v[:,14].max()),maximum_fluid_speed_m_s=float(v[:,13].max()),mean_wet_relative_speed_m_s=float(v[:,8].sum()/max(wet,1e-30)),wet_classification_change_area_at_10um_m2=float((v[:,9]+v[:,10]-2*v[:,11]).sum()),maximum_drag_bound_excess_n=float(excess.max()))
 rows.append(row)
f=np.array([r['normal_load_n'] for r in rows]);wet=np.array([r['wet_area_m2'] for r in rows]);d=np.diff(f);threshold=max(1.,.1*float(f.mean()));flags=[]
for i in np.flatnonzero(abs(d)>threshold):flags.append(dict(from_output=rows[i]['output_frame'],to_output=rows[i+1]['output_frame'],load_delta_n=float(d[i]),wet_area_delta_m2=float(wet[i+1]-wet[i]),mean_relative_speed_delta_m_s=rows[i+1]['mean_wet_relative_speed_m_s']-rows[i]['mean_wet_relative_speed_m_s'],status='REQUIRES_CONTEXTUAL_REVIEW'))
result=dict(native_consistency='PASS',force_samples=len(rows),normal_load_range_n=[float(f.min()),float(f.max())],wet_area_range_m2=[float(wet.min()),float(wet.max())],maximum_normal_load_step_n=float(abs(d).max()) if len(d) else 0.,temporal_screen_threshold_n=threshold,temporal_flags=flags,temporal_acceptance='PENDING_REVIEW' if flags else 'NO_LARGE_STEP_FLAG',release_approved=False,limits='Final exported evaluations only; not all Newton/substep states or a proof against visual jitter. Physical transients may exceed the screening threshold and require interpretation.',rows=rows)
a.output.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))
