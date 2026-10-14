"""Isolated constrained diagnostic after the free-translation gate; no integration."""
import sys,json,time,uuid
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import App,Utils
from frontend._force_field_ import ForceField
from cloth_next.gaia.water_push_prototype import quadratic_velocity,discrete_push
from tools.audit_water_response import sample
OUT=ROOT/'dist/water-push-cadence'

def reconstruct(points,accelerations):
    lo=points.min(axis=0)-.1;hi=points.max(axis=0)+.1
    axes=[np.linspace(lo[i],hi[i],n) for i,n in enumerate([5,13,13])]
    z,y,x=np.meshgrid(axes[2],axes[1],axes[0],indexing='ij');nodes=np.stack([x,y,z],axis=-1)
    distance=np.sum((nodes.reshape(-1,1,3)-points[None,:,:])**2,axis=-1)
    nearest=np.argpartition(distance,3,axis=1)[:,:4]
    d=np.take_along_axis(distance,nearest,axis=1);weights=1/np.maximum(d,1e-10);weights/=weights.sum(axis=1,keepdims=True)
    values=np.sum(accelerations[nearest]*weights[:,:,None],axis=1).reshape(nodes.shape).astype('f4')
    delivered=sample(values,lo,hi,points)
    return values,lo,hi,float(np.max(np.linalg.norm(delivered-accelerations,axis=1)))

def run():
    gate=json.loads((OUT/'control-24-0.01-0.0.json').read_text())
    if not next(m for m in gate['metrics'] if m['tau']==.1 and m['estimator']=='bdf2')['passed']:raise RuntimeError('Free-patch gate has not passed')
    if Utils.busy():raise RuntimeError('Separate native simulation is active')
    App.set_backend('cuda');app=App.create('cnx-push-constrained-'+uuid.uuid4().hex);scene=app.scene.create('sheet')
    n=9;tri=[]
    for z in range(n-1):
        for y in range(n-1):
            i=z*n+y;tri.extend([[i,i+1,i+n],[i+1,i+n+1,i+n]])
    names=['air_only','water_push','heterogeneous_push']
    for k,name in enumerate(names):
        points=np.array([[0,z*2/(n-1),-(y*2/(n-1)+k*4)] for z in range(n) for y in range(n)])
        app.asset.add.tri(name,points,np.array(tri));obj=scene.add(name).group(name)
        for key,value in {'density':1.,'young-mod':1000.,'poiss-rat':.4,'bend':.83,'strain-limit':.04,'force-field-weight':1.}.items():obj.param.set(key,value)
        obj.pin(list(range((n-1)*n,n*n))).pull(100.)
    s=app.session.create(scene.build(),'sheet')
    for key,value in {'frames':18,'fps':24.,'dt':.01,'gravity':[0,-9.81,0],'air-density':.001,'air-friction':.2,'disable-contact':True,'cg-tol':.001}.items():s.param.set(key,value)
    fixed=s.build();app.save();history=[];rows=[];poses=[]
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
            if frame is None or frame>=18:break
            positions=fixed.get.vertex(frame)[0].astype(float);t=frame/24
            history.append((t,positions.copy()));history=history[-3:];poses.append(positions)
            if len(history)==1:velocity=np.zeros_like(positions)
            elif len(history)==2:velocity=(positions-history[-2][1])/(t-history[-2][0])
            else:velocity=quadratic_velocity([p for _,p in history],[t for t,_ in history])
            field=ForceField()
            for k,name in enumerate(names):
                sl=slice(k*n*n,(k+1)*n*n);p=positions[sl];v=velocity[sl]
                # Same local physical water in air-only and push comparisons.
                water=np.tile([1.,0.,0.],(n*n,1))
                if k==2:water[:,0]=.5+np.clip((-p[:,2]-k*4)/2,0,1)
                lo=p.min(axis=0)-1.;hi=p.max(axis=0)+1.
                air=np.zeros((2,2,2,3),dtype='f4');air[...,0]=1.
                field.grid(air,lo,hi,kind='air-velocity',groups=[name])
                error=0.;a=np.zeros_like(v)
                if k:
                    a=discrete_push(water,v,np.ones(n*n),.1,1/24)
                    grid,glo,ghi,error=reconstruct(p,a);field.grid(grid,glo,ghi,kind='acceleration',groups=[name])
                initial=poses[0][sl];free=np.arange(n*n)<(n-1)*n
                rows.append({'case':name,'frame':frame,'time':t,'water_x_range':[float(water[:,0].min()),float(water[:,0].max())],'center_velocity':v[n*n//2].tolist(),'maximum_estimated_flow_ratio':float(np.max(v[free,0]/water[free,0])),'maximum_total_estimated_speed':float(np.max(np.linalg.norm(v,axis=1))),'mean_flow_displacement':float(np.mean(p[free,0]-initial[free,0])),'deformation_span_x':float(np.ptp(p[:,0])),'maximum_soft_pin_displacement':float(np.max(np.linalg.norm(p[~free]-initial[~free],axis=1))),'maximum_grid_delivery_error_m_s2':error,'maximum_intended_delta_v':float(np.max(np.linalg.norm(a/24,axis=1)))})
            fixed.update_force_field(field);field.clear();advance(frame+1)
        result={'native_root':str(fixed.session.app_root),'fixture':'2m x 2m; 9x9 vertices, 9 top-edge soft pins strength100, gravity -Y9.81; production material parameters; reduced topology diagnostic, not original 52x52 mesh','dt':.01,'control_hz':24,'tau':.1,'velocity_kind':'output-history estimate, no independent deformed-cloth instantaneous oracle','pin_force_available':False,'spatial_delivery':'4-neighbor inverse-distance grid reconstruction; error explicitly measured; not production-safe per-vertex coupling','rows':rows}
        (OUT/'constrained.json').write_text(json.dumps(result,indent=2));np.savez_compressed(OUT/'constrained-positions.npz',positions=np.array(poses))
        print(json.dumps({name:{'max_estimated_flow_ratio':max(r['maximum_estimated_flow_ratio'] for r in rows if r['case']==name),'last':next(r for r in reversed(rows) if r['case']==name)} for name in names},indent=2))
    finally:
        if fixed.is_running():
            fixed.save_and_quit();deadline=time.monotonic()+30
            while fixed.is_running() and time.monotonic()<deadline:time.sleep(.02)

if __name__=='__main__':run()
