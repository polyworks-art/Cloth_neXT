"""Unchanged official GAIA CUDA: partial immersion + actual FLIP coordinate audit."""
import io,json,sys,time,uuid,zipfile,zlib
from pathlib import Path
import numpy as np
from frontend import App
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.water_field import WaterVelocityField,solver_grid
from cloth_next.gaia.solver_fields import official_grid_payload
from cloth_next.ppf.coordinates import blender_vector_to_ppf,ppf_vector_to_blender
source=ROOT/'dist/water-coordinate-audit.gaia'
App.set_backend('cuda')
def run(name,p,tri,grid,frames=10):
    app=App.create('cnx-coordinate-'+name+'-'+uuid.uuid4().hex)
    solver_p=np.asarray([blender_vector_to_ppf(v) for v in p])
    app.asset.add.tri('cloth',solver_p,tri)
    scene=app.scene.create('water');scene.add('cloth').group('water')
    if grid is not None:
        lo,hi,values=grid
        scene.force_field.grid(values,lo,hi,kind='air-velocity',groups=['water'])
    session=app.session.create(scene.build(),'audit')
    session.param.set('frames',frames).set('fps',60.).set('dt',.001)
    session.param.set('gravity',[0.,0.,0.]).set('air-density',.1).set('air-friction',1.)
    fixed=session.build();started=time.monotonic();fixed.start(blocking=True)
    assert fixed.finished()
    poses=np.asarray([fixed.get.vertex(i)[0] for i in range(frames+1)])
    assert np.isfinite(poses).all()
    return poses,{'seconds':time.monotonic()-started,'session':fixed.session.app_root}
report={}
# One disconnected cloth mesh: an inside patch and outside patch, without
# mechanical coupling that could pull the outside vertices indirectly.
p=np.asarray([[x,y,z] for x in (0.,2.) for y in (-.2,0.,.2) for z in (-.2,0.,.2)])
tri=[]
for offset in (0,9):
    for y in range(2):
        for z in range(2):
            a=offset+3*y+z;tri += [[a,a+1,a+4],[a,a+4,a+3]]
tri=np.asarray(tri)
v=np.zeros((3,3,3,3),dtype=np.float32);v[...,0]=2.
field=WaterVelocityField((-.5,-1.,-1.),(.5,1.,1.),v,np.ones((3,3,3),np.float32))
baseline,a=run('synthetic-control',p,tri,None)
water,b=run('synthetic-flow',p,tri,solver_grid(field))
delta=water[-1]-baseline[-1]
inside=float(delta[:9,0].mean());outside=float(np.abs(delta[9:]).max())
assert inside>.01 and outside<1e-6
report['synthetic']={'inside_mean_x':inside,'outside_max_delta':outside,'control':a,'flow':b,'result':'PASS','scope':'two disconnected patches of one cloth mesh, one inside the constant +X box and one outside'}
print('SYNTHETIC',json.dumps(report['synthetic']),flush=True)
with zipfile.ZipFile(source) as archive:
    fixture=np.load(io.BytesIO(archive.read('fixture/Plane.npz')))
    p,tri=fixture['positions'],fixture['triangles']
with GaiaContainer(source) as container:
    field=container.frame(0)
    payload=official_grid_payload(container,[0])['grids'][0]
    values=np.frombuffer(zlib.decompress(payload['data']),dtype='<f4').reshape(payload['shape'])[0]
# Place the original source topology in occupied water; record the fixture
# translation explicitly rather than modifying the user scene.
z,y,x=np.unravel_index(np.argmax(field.contribution[...,0]),field.influence.shape)
center=np.asarray(field.minimum)+(np.asarray(field.maximum)-field.minimum)*[x,y,z]/(np.asarray(field.velocity.shape[:3][::-1])-1)
offset=center-p.mean(0);p=p+offset
baseline,c=run('flip-control',p,tri,None,frames=4)
water,d=run('flip-flow',p,tri,(payload['min'],payload['max'],values),frames=4)
delta=water[-1]-baseline[-1]
mean=float(delta[:,0].mean());peak=float(delta[:,0].max())
assert mean>1e-5 and peak>1e-4
report['real_flip']={'mean_x':mean,'peak_x':peak,'offset':offset.tolist(),'frame':150,'control':c,'flow':d,'result':'PASS','scope':'original Plane topology translated into supported liquid, no pins/collider/gravity'}
print('REAL FLIP',json.dumps(report['real_flip']),flush=True)
with zipfile.ZipFile(source,'a',compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('audit/solver-results.json',json.dumps(report,indent=2))
    buf=io.BytesIO();np.savez_compressed(buf,baseline=baseline,flow=water)
    archive.writestr('audit/real-trajectories.npz',buf.getvalue())
(ROOT/'dist/water-coordinate-solver.json').write_text(json.dumps(report,indent=2))
