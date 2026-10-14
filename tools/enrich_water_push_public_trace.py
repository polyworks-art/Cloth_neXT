"""Reconstruct uploaded spatial means from saved public output APIs, not checkpoints."""
import sys,json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import App
from cloth_next.gaia.water_push_prototype import quadratic_velocity,discrete_push

def run():
    App.set_backend('cuda')
    for path in (ROOT/'dist/water-push-cadence').glob('control-discrete-*.json'):
        if path.name.endswith('-progress.json'):continue
        r=json.loads(path.read_text());fixed=App.load(Path(r['native_root']).name).session.select('cadence').fixed_session;history=[]
        for hold in r['holds']:
            frame=hold['held_frame'];pose=fixed.get.vertex(frame)[0].astype(float);time=frame/r['control_hz'];history.append((time,pose));history=history[-3:]
            if len(history)==1:velocity=np.zeros_like(pose)
            elif len(history)==2:velocity=(pose-history[-2][1])/(history[-1][0]-history[-2][0])
            else:velocity=quadratic_velocity([x for _,x in history],[t for t,_ in history])
            for k,case in enumerate(r['metrics']):
                assert case['estimator']=='bdf2' and case['controller']=='discrete'
                row=next(row for row in r['records'] if row['frame']==frame and row['case']==k)
                np.testing.assert_allclose(row['estimate'],velocity[k*9+4],atol=1e-9)
                a=discrete_push(np.tile([1,0,0],(9,1)),velocity[k*9:(k+1)*9],np.ones(9),case['tau'],1/r['control_hz'])
                row['uploaded_acceleration']=a.mean(axis=0).astype('f4').tolist()
                row['uploaded_trace_provenance']='Reconstructed from every public output vertex and unchanged controller; native field is the float32 spatial mean'
        path.write_text(json.dumps(r,indent=2))
    print('Uploaded field means reconstructed and center estimators cross-checked')

if __name__=='__main__':run()
