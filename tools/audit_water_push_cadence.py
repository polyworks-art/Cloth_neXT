"""Public output-only estimator and cadence experiment; no production wiring."""
import sys,json,time,uuid,argparse,hashlib
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import App,Utils
from frontend._force_field_ import ForceField
from cloth_next.gaia.water_push_prototype import output_velocity,quadratic_velocity,limited_push,discrete_push
OUT=ROOT/'dist/water-push-cadence';OUT.mkdir(exist_ok=True)
TOLERANCE=.01
MEASUREMENT_HZ=24. # Each experiment uses its actual public output/control FPS.


def run(args):
    global MEASUREMENT_HZ
    MEASUREMENT_HZ=float(args.hz)
    if args.air_density!=0:raise ValueError('Independent momentum oracle requires drag-free translating fixture')
    if Utils.busy():raise RuntimeError('Separate native simulation is active')
    App.set_backend('cuda');app=App.create('cnx-push-cadence-'+uuid.uuid4().hex)
    scene=app.scene.create('cadence');tri=[]
    for z in range(2):
        for y in range(2):
            i=z*3+y;tri.extend([[i,i+1,i+3],[i+1,i+4,i+3]])
    if args.proof:cases=[{'tau':1.,'estimator':'secant','controller':'analytic-acceleration'}]
    else:
        # Full estimator/ratio comparison at 24 Hz; higher rates validate BDF2.
        estimators=args.estimators or (['secant','bdf2','quadratic-hold'] if args.hz==24 else ['bdf2'])
        cases=[{'tau':tau,'estimator':estimator,'controller':controller}
               for tau in [1.,.1,.05] for estimator in estimators
               for controller in ([args.controller] if args.controller!='both' else ['old','discrete'])]
        if args.ratios and args.hz==24:
            cases += [{'tau':ratio/args.hz,'estimator':'quadratic-hold','controller':'discrete','ratio':ratio} for ratio in [1,2,3,4,5,8,10]]
    for k,case in enumerate(cases):
        p=np.array([[0,z*.1,-(y*.1+k*2)] for z in range(3) for y in range(3)])
        name=f'patch{k}';app.asset.add.tri(name,p,np.array(tri));obj=scene.add(name).group(name)
        for key,value in {'density':1.,'young-mod':1000.,'poiss-rat':.4,'bend':.83,'strain-limit':.04,'force-field-weight':1.}.items():obj.param.set(key,value)
    session=app.session.create(scene.build(),'cadence')
    count=round(args.duration*MEASUREMENT_HZ)
    params={'frames':count,'fps':MEASUREMENT_HZ,'dt':args.dt,'gravity':[0,0,0],'air-density':0. if args.proof else args.air_density,'air-friction':.2,'disable-contact':True,'cg-tol':args.cg_tol}
    for key,value in params.items():session.param.set(key,value)
    fixed=session.build();app.save();records=[];holds=[];history=[];requested=0
    prefix=f"{'proof' if args.proof else 'control-'+args.controller}-{args.hz}-{args.dt}-{args.air_density}"
    def advance(frame):
        for attempt in range(12):
            held=fixed.held_frame()
            if held is not None and held>=frame:return
            try:
                fixed.run_until_frame(frame,timeout=120.,poll=.02)
                return
            except PermissionError:
                if attempt==11:raise
                time.sleep(.025)
    def clock():
        values=fixed.get.log.numbers('clock')
        return float(values[-1][0]) if values else 0.
    velocity=np.zeros((len(cases)*9,3));last_acceleration=velocity.copy();last_hold=0.
    def oracle(frame,held_time):
        nonlocal velocity,last_hold
        final_dt=fixed.get.log.numbers('final-dt')
        dt=float(final_dt[-1][1]) if final_dt else 0.
        velocity+=last_acceleration*(held_time-last_hold)
        last_hold=held_time
        return velocity.copy(),True,dt
    started=time.monotonic()
    try:
        advance(0)
        while True:
            boundary=fixed.held_frame()
            if boundary is None:
                if fixed.finished():break
                raise RuntimeError('Missing authoritative hold')
            if boundary>=count:break
            current,identity=fixed.get.vertex(boundary)
            assert identity==boundary
            held_time=clock();sample_time=boundary/MEASUREMENT_HZ
            reference,valid,last_dt=oracle(boundary,held_time)
            history.append((sample_time,current.copy()))
            if len(history)>3:history.pop(0)
            requested_time=requested/MEASUREMENT_HZ
            holds.append({'requested_frame':requested,'held_frame':boundary,'requested_time':requested_time,'output_time':sample_time,'actual_native_hold_time':held_time,'overshoot_s':held_time-requested_time,'last_native_dt':last_dt})
            # Next exact control-grid time strictly after the actual native clock.
            next_tick=int(np.floor((held_time+1e-9)*args.hz))+1
            next_requested=min(count,round(next_tick*MEASUREMENT_HZ/args.hz))
            nominal_interval=1/args.hz
            field=ForceField()
            for k,case in enumerate(cases):
                sl=slice(k*9,(k+1)*9)
                if len(history)==1:estimate=np.zeros((9,3))
                elif case['estimator']=='secant' or len(history)<3:
                    estimate=(history[-1][1][sl].astype(float)-history[-2][1][sl])/(history[-1][0]-history[-2][0])
                else:
                    estimate=quadratic_velocity([p[sl] for _,p in history],[t for t,_ in history],held_time if case['estimator']=='quadratic-hold' else sample_time)
                if np.ptp(estimate,axis=0).max()>.01:raise RuntimeError('Uniform spatial field lost validity')
                water=np.tile([1.,0.,0.],(9,1));support=np.ones(9)
                if args.proof:acceleration=np.tile([1.,0.,0.],(9,1))
                elif case['controller']=='old':acceleration=limited_push(water,estimate,support,case['tau'],nominal_interval+args.dt)[1]
                else:acceleration=discrete_push(water,estimate,support,case['tau'],nominal_interval)
                last_acceleration[sl]=acceleration.mean(axis=0).astype('f4')
                air=np.zeros((2,2,2,3),dtype='f4');air[...,0]=1.
                accel=np.broadcast_to(acceleration.mean(axis=0),(2,2,2,3)).astype('f4').copy()
                field.grid(air,[-1000]*3,[1000]*3,kind='air-velocity',groups=[f'patch{k}'])
                field.grid(accel,[-1000]*3,[1000]*3,kind='acceleration',groups=[f'patch{k}'])
                stats=fixed.get.statistics(boundary)['objects'][f'patch{k}']['values']
                records.append({'case':k,'frame':boundary,'time':held_time,'sample_time':sample_time,'estimate':estimate[4].tolist(),'reference':reference[sl][4].tolist(),'statistic_velocity':[stats['velocity_'+axis] or 0. for axis in 'xyz'],'position':current[k*9+4].tolist(),'acceleration':acceleration[4].tolist(),'uploaded_acceleration':last_acceleration[sl][4].tolist(),'error':float(np.linalg.norm(estimate[4]-reference[sl][4])),'reference_is_known_force_impulse':valid})
            fixed.update_force_field(field);field.clear()
            requested=next_requested
            (OUT/(prefix+'-progress.json')).write_text(json.dumps({'cases':cases,'holds':holds,'records':records},indent=2))
            advance(requested)
        # Known force impulse is independent of position-history estimates.
        final_time=clock()
        final_reference,_,_=oracle(fixed.get.latest_frame(),final_time)
        reference_rows=[]
        final_dt=fixed.get.log.numbers('final-dt') or []
        step_start=np.array([row[0] for row in final_dt],dtype=float)
        step_end=step_start+np.array([row[1] for row in final_dt],dtype=float)
        for hold in holds:
            rows=[r for r in records if r['frame']==hold['held_frame']]
            reference_rows.append({'time':hold['actual_native_hold_time'],'velocity_x':[r['reference'][0] for r in rows]})
        reference_rows.append({'time':final_time,'velocity_x':[float(final_reference[k*9+4,0]) for k in range(len(cases))]})
        metrics=[]
        for k,case in enumerate(cases):
            rows=[r for r in records if r['case']==k];peak=max(reference_rows,key=lambda r:r['velocity_x'][k])
            before=[r for r in rows if r['time']<peak['time']-1e-7];preceding=before[-1] if before else rows[0]
            metrics.append(dict(case,case=k,maximum_speed_ratio=peak['velocity_x'][k],peak_time=peak['time'],maximum_estimated_ratio=max(r['estimate'][0] for r in rows),mean_error=float(np.mean([r['error'] for r in rows])),maximum_error=max(r['error'] for r in rows),error_before_peak=preceding['error'],acceleration_before_peak=preceding['acceleration'],passed=peak['velocity_x'][k]<=1+TOLERANCE))
        report={'classification':'public-output-only','measurement_hz':MEASUREMENT_HZ,'control_hz':args.hz,'dt':args.dt,'cg_tolerance':args.cg_tol,'air_density':args.air_density,'tolerance':TOLERANCE,'duration':args.duration,'seconds':time.monotonic()-started,'native_root':str(fixed.session.app_root),'metrics':metrics,'holds':holds,'records':records,'reference_trace':reference_rows}
        report['reference_kind']='Known acceleration impulse at actual held native time; free translation, zero drag'
        report['actual_native_dt_range']=[float(np.min(np.diff(step_start))) if len(step_start)>1 else 0.,float(np.max(step_end-step_start))]
        # Cross-check the independent force-impulse oracle against public positions.
        # Semi-implicit uniform translation: v+=a*dt; x+=v*dt.
        expected=[];observed=[]
        for k in range(len(cases)):
            rows=[r for r in records if r['case']==k]
            x=0.;v=0.;cursor=0;xs=[0.];ts=[0.]
            for start,dt in final_dt:
                while cursor+1<len(rows) and rows[cursor+1]['time']<=float(start)+1e-6:cursor+=1
                v+=rows[cursor]['uploaded_acceleration'][0]*float(dt);x+=v*float(dt)
                ts.append(float(start)+float(dt));xs.append(x)
            case_errors=[]
            for row in rows:
                prediction=float(np.interp(row['sample_time'],ts,xs))
                expected.append(prediction);observed.append(row['position'][0])
                case_errors.append(abs(prediction-row['position'][0]))
            metrics[k]['oracle_position_residual_m']=max(case_errors)
            metrics[k]['oracle_validated']=max(case_errors)<=2e-4
            metrics[k]['gate_status']='pass' if metrics[k]['passed'] and metrics[k]['oracle_validated'] else ('overshoot' if not metrics[k]['passed'] else 'reference-inconclusive')
        report['momentum_oracle_position_residual_m']=float(np.max(np.abs(np.array(expected)-observed)))
        report['oracle_position_validation_passed']=report['momentum_oracle_position_residual_m']<=2e-4
        if args.proof:
            report['analytic_secant_error']=max(r['error'] for r in records)
        (OUT/(prefix+'.json')).write_text(json.dumps(report,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k not in {'holds','records','reference_trace'}}),flush=True)
    finally:
        if fixed.is_running():
            fixed.save_and_quit();deadline=time.monotonic()+30
            while fixed.is_running() and time.monotonic()<deadline:time.sleep(.02)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--dt',type=float,default=.01);parser.add_argument('--hz',type=int,default=24)
    parser.add_argument('--duration',type=float,default=1.25);parser.add_argument('--air-density',type=float,default=0.)
    parser.add_argument('--controller',choices=['old','discrete','both'],default='both')
    parser.add_argument('--cg-tol',type=float,default=.001)
    parser.add_argument('--estimators',nargs='+',choices=['secant','bdf2','quadratic-hold'])
    parser.add_argument('--proof',action='store_true');parser.add_argument('--ratios',action='store_true')
    run(parser.parse_args())
