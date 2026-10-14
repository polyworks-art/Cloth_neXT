"""Fail-first native free-patch safety gate; no production wiring or release."""
import sys,json,time,uuid,argparse,hashlib,csv
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import App,Utils
from frontend._force_field_ import ForceField
from cloth_next.gaia.water_push_prototype import limited_push,output_velocity
OUT=ROOT/'dist/water-push-prototype';OUT.mkdir(exist_ok=True)
OVERSHOOT_TOLERANCE=.01 # Declared before native runs: maximum 1%.


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--dt',type=float,default=.01)
    parser.add_argument('--air-density',type=float,default=.001);args=parser.parse_args()
    if Utils.busy():raise RuntimeError('A foreign native simulation is active')
    App.set_backend('cuda');app=App.create('cnx-water-push-'+uuid.uuid4().hex)
    scene=app.scene.create('gate');responses=[1.,.1,.05];tri=[]
    for z in range(2):
        for y in range(2):
            i=z*3+y;tri.extend([[i,i+1,i+3],[i+1,i+4,i+3]])
    for k in range(3):
        p=np.array([[0,z*.1,-(y*.1+k*2)] for z in range(3) for y in range(3)])
        name=f'patch{k}';app.asset.add.tri(name,p,np.array(tri));obj=scene.add(name).group(name)
        for key,val in {'density':1.,'young-mod':1000.,'poiss-rat':.4,'bend':.83,'strain-limit':.04,'force-field-weight':1.}.items():obj.param.set(key,val)
    session=app.session.create(scene.build(),'gate')
    for key,val in {'frames':30,'fps':24.,'dt':args.dt,'gravity':[0,0,0],'air-density':args.air_density,'air-friction':.2,'disable-contact':True,'cg-tol':.001}.items():session.param.set(key,val)
    fixed=session.build();records=[];positions=[];previous=None;old_boundary=None
    native=ROOT/'dist/gaia-streaming/official/target/cuda/release/ppf-cts-server.exe'
    before=hashlib.sha256(native.read_bytes()).hexdigest()
    def advance(frame):
        for attempt in range(8):
            try:
                fixed.run_until_frame(frame,timeout=120.)
                return
            except PermissionError:
                # Retry only the public hold operation: Windows can briefly
                # lock its marker while the owned solver reads it.
                if attempt==7:raise
                time.sleep(.025)
    try:
        advance(0)
        while True:
            boundary=fixed.held_frame()
            if boundary is not None and boundary>=30:break
            if boundary is None:
                if fixed.finished():break
                raise RuntimeError('No authoritative held boundary')
            current,actual=fixed.get.vertex(boundary)
            if actual!=boundary:raise RuntimeError('Output identity mismatch')
            if previous is None:estimated=np.zeros_like(current)
            else:estimated=output_velocity(current,previous,boundary,old_boundary,24.)
            stats=fixed.get.statistics(boundary)
            field=ForceField()
            for k,response in enumerate(responses):
                # Uniform fixture only: reject heterogeneous velocity. A general
                # per-vertex spatial reconstruction is deliberately not claimed.
                selection=slice(k*9,(k+1)*9);v=estimated[selection]
                if np.ptp(v,axis=0).max()>1e-3:raise RuntimeError('Uniform spatial-delivery gate lost homogeneity')
                water=np.tile([1.,0.,0.],(9,1));support=np.ones(9)
                # Bound prospective native overshoot by one maximum solver step.
                window=1/24.+args.dt
                desired,applied=limited_push(water,v,support,response,window)
                air=np.zeros((2,2,2,3),dtype='f4');air[...,0]=1.
                accel=np.broadcast_to(applied.mean(axis=0),(2,2,2,3)).astype('f4').copy()
                field.grid(air,[-10]*3,[10]*3,kind='air-velocity',groups=[f'patch{k}'])
                field.grid(accel,[-10]*3,[10]*3,kind='acceleration',groups=[f'patch{k}'])
                values=stats['objects'][f'patch{k}']['values']
                records.append({'frame':boundary,'time':stats['time'],'response_time':response,'position':current[k*9+4].tolist(),'estimated_velocity':v[4].tolist(),'actual_object_velocity':[values['velocity_'+axis] or 0. for axis in 'xyz'],'water_velocity':[1.,0.,0.],'relative_velocity':(water[4]-v[4]).tolist(),'desired_acceleration':desired[4].tolist(),'applied_acceleration':applied[4].tolist(),'occupancy':1.,'prospective_window_upper_bound':window})
            (OUT/f'progress-{args.dt}-{args.air_density}.json').write_text(json.dumps(records,indent=2))
            fixed.update_force_field(field);field.clear()
            positions.append(current.copy());previous=current.copy();old_boundary=boundary
            advance(boundary+1)
        # Capture final endpoint statistics as well.
        final=fixed.get.latest_frame();stats=fixed.get.statistics(final)
        for k,response in enumerate(responses):
            values=stats['objects'][f'patch{k}']['values']
            records.append({'frame':final,'time':stats['time'],'response_time':response,'actual_object_velocity':[values['velocity_'+axis] for axis in 'xyz']})
    finally:
        if fixed.is_running():
            fixed.save_and_quit()
            deadline=time.monotonic()+30
            while fixed.is_running() and time.monotonic()<deadline:time.sleep(.02)
    assert before==hashlib.sha256(native.read_bytes()).hexdigest()
    cases=[]
    for response in responses:
        rows=[r for r in records if r['response_time']==response]
        ratio=max(r['actual_object_velocity'][0] for r in rows)
        cases.append({'response_time':response,'max_projected_speed_ratio':ratio,'overshoot':max(0.,ratio-1.),'passed':ratio<=1+OVERSHOOT_TOLERANCE})
    report={'dt':args.dt,'air_density':args.air_density,'tolerance':OVERSHOOT_TOLERANCE,'cases':cases,'passed':all(r['passed'] for r in cases),'native_sha256':before,'native_root':str(fixed.session.app_root),'records':records}
    (OUT/f'gate-{args.dt}-{args.air_density}.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='records'}),flush=True)
if __name__=='__main__':main()
