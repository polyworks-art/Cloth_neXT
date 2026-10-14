"""Unchanged official CUDA solver: physical velocity, weight, mass and orientation sweep."""
import sys,json,time,uuid,csv
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'dist/gaia-streaming/official'))
from frontend import App,Utils
OUT=ROOT/'dist/water-response';OUT.mkdir(exist_ok=True)
def run(speed=1.,weight=1.,density=1.,normal='X',air_density=.001,dt=.001,cases=None):
    if Utils.busy():raise RuntimeError('A separate native simulation is active; refusing to adopt it')
    if cases is None:cases=[{'speed':speed,'weight':weight,'density':density,'normal':normal}]
    tri=[]
    for z in range(2):
        for y in range(2):
            i=z*3+y;tri.extend([[i,i+1,i+3],[i+1,i+4,i+3]])
    # Blender -> solver coordinates, matching the production transform.
    app=App.create('cnx-response-'+uuid.uuid4().hex,cache_dir=str(OUT/'native-cache'))
    scene=app.scene.create('response')
    for index,case in enumerate(cases):
        speed=case.get('speed',1.);weight=case.get('weight',1.);density=case.get('density',1.);normal=case.get('normal','X')
        p=np.array([[0,y*.1,z*.1] for z in range(3) for y in range(3)],dtype=float)
        if normal=='Z':p=p[:,[1,2,0]]
        p[:,1]+=index*2
        p=p[:,[0,2,1]];p[:,2]*=-1
        name=f'patch{index}';app.asset.add.tri(name,p,np.array(tri));obj=scene.add(name).group(name)
        for key,val in {'density':density,'young-mod':1000.,'poiss-rat':.4,'bend':.83,'strain-limit':.04,'force-field-weight':weight,'deformation-damping':0.,'bending-damping':0.}.items():obj.param.set(key,val)
        values=np.zeros((2,2,2,3),dtype=np.float32);values[...,0]=speed
        scene.force_field.grid(values,[-1000]*3,[1000]*3,kind='air-velocity',groups=[name])
    session=app.session.create(scene.build(),'response')
    for key,val in {'frames':72,'fps':24.,'dt':dt,'gravity':[0,0,0],'air-density':air_density,'air-friction':.2,'isotropic-air-friction':0.,'disable-contact':True,'cg-tol':.001}.items():session.param.set(key,val)
    fixed=session.build();started=time.monotonic();fixed.start(blocking=True)
    if not fixed.finished():raise RuntimeError('Native controlled simulation did not finish')
    poses=np.array([fixed.get.vertex(i)[0] for i in range(73)])
    poses=poses[:,:,[0,2,1]];poses[:,:,1]*=-1
    results=[]
    for index,case in enumerate(cases):
        mask=(poses[0,:,1]>=index*2-1e-4)&(poses[0,:,1]<=index*2+.2001)
        assert int(mask.sum())==9, 'Expected nine independently identified patch vertices'
        results.append(measure(poses[:,mask],case,air_density,dt,time.monotonic()-started,str(fixed.session.app_root)))
    return results
def measure(poses,case,air_density,dt,seconds,native_root):
    speed=case.get('speed',1.);weight=case.get('weight',1.);density=case.get('density',1.);normal=case.get('normal','X')
    center=int(np.argmin(np.linalg.norm(poses[0]-poses[0].mean(axis=0),axis=1)))
    c=poses[:,center];v=np.gradient(c,1/24,axis=0)
    v[0]=0. # The controlled initial state is explicitly stationary.
    a=np.gradient(v,1/24,axis=0)
    relative=np.array([speed,0,0])-v
    flow_alignment=v[:,0]/np.maximum(np.linalg.norm(v,axis=1),1e-12)
    acceleration_alignment=np.sum(a*relative,axis=1)/np.maximum(np.linalg.norm(a,axis=1)*np.linalg.norm(relative,axis=1),1e-12)
    ratio=v[:,0]/speed;rel=np.linalg.norm(np.array([speed,0,0])-v,axis=1)
    name=f'speed{speed:g}-weight{weight:g}-mass{density:g}-normal{normal}-air{air_density:g}-dt{dt:g}'
    with (OUT/(name+'.csv')).open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['frame','time_s','cloth_x','cloth_y','cloth_z','vx','vy','vz','ax','ay','az','water_speed','effective_target_speed','velocity_ratio','relative_speed','occupancy','water_x','water_y','water_z','relative_x','relative_y','relative_z','flow_alignment','acceleration_alignment'])
        for j in range(len(poses)):writer.writerow([j,j/24,*c[j],*v[j],*a[j],speed,speed*weight,ratio[j],rel[j],1.,speed,0.,0.,*relative[j],flow_alignment[j],acceleration_alignment[j]])
    thresholds={str(int(q*100)):next((j/24 for j in range(1,len(poses)) if ratio[j]>=q and v[j,0]>0),None) for q in [.1,.25,.5,.75,.9,1-1/np.e]}
    result={'case':name,'speed':speed,'weight':weight,'surface_density':density,'normal':normal,'air_density':air_density,'air_friction':.2,'dt':dt,'seconds':seconds,'mean_local_water_speed':speed,'final_cloth_speed':float(np.linalg.norm(v[-1])),'final_velocity_ratio':float(ratio[-1]),'threshold_times_s':thresholds,'minimum_relative_speed':float(rel[1:].min()),'peak_acceleration':float(np.linalg.norm(a,axis=1).max()),'mean_flow_alignment':float(np.mean(v[1:,0]/np.maximum(np.linalg.norm(v[1:],axis=1),1e-12))),'follows_source_flow':bool(abs(ratio[-1]-1)<.1),'native_root':native_root}
    result['threshold_frames']={k:None if t is None else round(t*24) for k,t in thresholds.items()}
    result['final_effective_velocity_ratio']=result['final_velocity_ratio']/weight
    result['normal_flow_angle_degrees']=0. if normal=='X' else 90.
    result['duration_s']=(len(poses)-1)/24
    print('CONTROLLED_RESULT '+json.dumps(result),flush=True)
    return result
def main():
    App.set_backend('cuda');results=[]
    cases=[{'speed':s} for s in [.5,1.,2.,4.]]
    cases += [{'weight':w} for w in [2.,5.,10.,25.,50.,100.]]
    cases += [{'density':m} for m in [.1,10.]]
    cases += [{'normal':'Z'}]
    results.extend(run(cases=cases));(OUT/'controlled-summary.json').write_text(json.dumps(results,indent=2))
    for params in [{'air_density':.01},{'dt':.002},{'dt':.01}]:
        results.extend(run(**params));(OUT/'controlled-summary.json').write_text(json.dumps(results,indent=2))
if __name__=='__main__':main()
