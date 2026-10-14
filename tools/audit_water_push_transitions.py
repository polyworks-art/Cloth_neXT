"""Public held-field transition tests; known free-translation impulse reference."""
import sys,json,time,uuid
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import App,Utils
from frontend._force_field_ import ForceField
from cloth_next.gaia.water_push_prototype import quadratic_velocity,discrete_push
OUT=ROOT/'dist/water-push-cadence'

def water(k,t):
    # Source frames at 24 fps; interpolate existing endpoints, never rebake.
    source=np.arange(25)/24
    if k==0:values=np.where(source[:,None]<.25,[.5,0,0],[1.,0,0])
    elif k==1:values=np.where(source[:,None]<.375,[1.,0,0],[0.,0,1.])
    else:values=np.tile([(.5 if k==3 else 2.),0,0],(25,1))
    frame=min(int(np.floor(t*24)),23);alpha=np.clip((t-source[frame])*24,0,1)
    v=values[frame]*(1-alpha)+values[frame+1]*alpha
    support=1.
    if k==2:
        support=float(.125<=t<.5)
        v=np.array([1.,0,0])
    if k==5:v=np.array([0.,0,0]);support=0.
    return v,support,frame,alpha

def run():
    if Utils.busy():raise RuntimeError('Separate native simulation is active')
    App.set_backend('cuda');app=App.create('cnx-push-transitions-'+uuid.uuid4().hex);scene=app.scene.create('transitions')
    tri=[[0,1,3],[1,4,3],[1,2,4],[2,5,4],[3,4,6],[4,7,6],[4,5,7],[5,8,7]]
    names=['speed_change','direction_change','enter_leave','region_slow','region_fast','unrelated_dry']
    for k,name in enumerate(names):
        p=np.array([[0,z*.1,-(y*.1+k*2)] for z in range(3) for y in range(3)])
        app.asset.add.tri(name,p,np.array(tri));obj=scene.add(name).group(name)
        for key,value in {'density':1.,'young-mod':1000.,'poiss-rat':.4,'bend':.83,'strain-limit':.04,'force-field-weight':1.}.items():obj.param.set(key,value)
    s=app.session.create(scene.build(),'transitions')
    for key,value in {'frames':72,'fps':96.,'dt':.01,'gravity':[0,0,0],'air-density':0.,'disable-contact':True,'cg-tol':.00001}.items():s.param.set(key,value)
    fixed=s.build();app.save();history=[];rows=[];applied=[];native_steps_seen=0;reference=np.zeros((6,3))
    def advance(frame):
        for attempt in range(12):
            try:fixed.run_until_frame(frame,timeout=180.,poll=.02);return
            except PermissionError:
                if attempt==11:raise
                time.sleep(.025)
    try:
        advance(0)
        while not fixed.finished():
            frame=fixed.held_frame()
            if frame is None or frame>=72:break
            p=fixed.get.vertex(frame)[0];t=frame/96;held=float(fixed.get.log.numbers('clock')[-1][0])
            steps=fixed.get.log.numbers('final-dt') or []
            if applied:
                start,a0,a1=applied[-1]
                for st,dt in steps[native_steps_seen:]:
                    alpha=np.clip((st-start)*96,0,1);reference+=((1-alpha)*a0+alpha*a1)*dt
            native_steps_seen=len(steps)
            history.append((t,p.astype(float)));history=history[-3:]
            if len(history)==1:estimate=np.zeros_like(p)
            elif len(history)==2:estimate=(p-history[-2][1])/(t-history[-2][0])
            else:estimate=quadratic_velocity([pos for _,pos in history],[time for time,_ in history])
            field=ForceField();a0=[];a1=[]
            for k,name in enumerate(names):
                v=estimate[k*9+4:k*9+5];w,support,source,alpha=water(k,held);wn,sn,_,_=water(k,held+1/96)
                a=discrete_push(w[None,:],v,[support],.1,1/96)[0];an=discrete_push(wn[None,:],v,[sn],.1,1/96)[0]
                a0.append(a);a1.append(an)
                values=np.broadcast_to(np.stack([a,an])[:,None,None,None,:],(2,2,2,2,3)).astype('f4').copy()
                if k!=5:field.grid(values,[-1000]*3,[1000]*3,times=[held,held+1/96],kind='acceleration',groups=[name])
                # An unrelated uniform acceleration is preserved in dry case.
                if k==5:
                    own=np.zeros((2,2,2,3),dtype='f4');own[...,1]=.5;field.grid(own,[-1000]*3,[1000]*3,groups=[name]);a0[-1]=a1[-1]=np.array([0,.5,0])
                direction=w/np.linalg.norm(w) if np.linalg.norm(w)>0 else np.zeros(3)
                rows.append({'case':name,'time':held,'output_time':t,'water':w.tolist(),'support':support,'source_frame_pair':[source+1,source+2],'source_alpha':alpha,'estimated_velocity':v[0].tolist(),'reference_velocity':reference[k].tolist(),'flow_projection':float(reference[k]@direction),'water_speed':float(np.linalg.norm(w)),'intended_delta_v':(a/96).tolist(),'uploaded_acceleration_start':np.array(a0[-1]).astype('f4').tolist(),'uploaded_acceleration_next':np.array(a1[-1]).astype('f4').tolist(),'position':p[k*9+4].tolist()})
            applied.append((held,np.array(a0).astype('f4'),np.array(a1).astype('f4')))
            fixed.update_force_field(field);field.clear();next_frame=min(72,int(np.floor((held+1e-9)*96))+1);advance(next_frame)
        result={'native_root':str(fixed.session.app_root),'control_hz':96,'source_hz':24,'dt':.01,'tau':.1,'rows':rows,'notes':'Reference integrates known spatially uniform field at public native step times. Changing-direction old momentum is retained; scalar total-speed cap is never used. Enter/leave stops only Water Push. Different spatial regions use separate public group fields, not a claim about heterogeneous deforming cloth.'}
        result['position_reference_residuals']={}
        steps=fixed.get.log.numbers('final-dt')
        for name in names:
            samples=[row for row in rows if row['case']==name];x=np.array(samples[0]['position']);v=np.zeros(3);cursor=0;xs=[x.copy()];ts=[0.]
            for start,dt in steps:
                while cursor+1<len(samples) and samples[cursor+1]['time']<=start+1e-6:cursor+=1
                row=samples[cursor];alpha=np.clip((start-row['time'])*96,0,1)
                acceleration=(1-alpha)*np.array(row['uploaded_acceleration_start'])+alpha*np.array(row['uploaded_acceleration_next'])
                v+=acceleration*dt;x+=v*dt;xs.append(x.copy());ts.append(start+dt)
            xs=np.array(xs)
            error=max(float(np.linalg.norm(np.array(row['position'])-np.array([np.interp(row['output_time'],ts,xs[:,axis]) for axis in range(3)]))) for row in samples)
            result['position_reference_residuals'][name]={'maximum_m':error,'passed':error<=2e-4}
        (OUT/'transitions.json').write_text(json.dumps(result,indent=2));print('Transition fixture complete')
    finally:
        if fixed.is_running():
            fixed.save_and_quit();deadline=time.monotonic()+30
            while fixed.is_running() and time.monotonic()<deadline:time.sleep(.02)

if __name__=='__main__':run()
