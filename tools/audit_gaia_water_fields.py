"""Serial official CUDA field semantics probes, stored in a .gaia container."""
import argparse
import json
from pathlib import Path
import time
import uuid
import zipfile
import numpy as np
from frontend import App


def run_case(name, kind=None, speed=0., outside=False, spatial=False, moving=False):
    app = App.create('cnx-water-'+name+'-'+uuid.uuid4().hex)
    # Solver Y up: a vertical YZ sheet, normal along the flow's X axis.
    vertices = np.asarray([[0,y,z] for y in (-.2,0,.2) for z in (-.2,0,.2)],float)
    tri=[]
    for y in range(2):
        for z in range(2):
            a=3*y+z
            tri += [[a,a+1,a+4],[a,a+4,a+3]]
    app.asset.add.tri('cloth',vertices,np.asarray(tri))
    scene=app.scene.create('water')
    cloth=scene.add('cloth').group('water')
    cloth.param.set('density',1.)
    if moving: cloth.velocity(2.,0.,0.)
    lo,hi=([-2,-2,-2],[2,2,2])
    if outside: lo,hi=([10,10,10],[12,12,12])
    if moving: lo,hi=([.2,-2,-2],[.6,2,2])
    if kind:
        values=np.zeros((2,2,2,3),np.float32)
        values[...,1 if moving else 0]=speed
        if spatial: values[:,0,:,0]=speed*.1
        scene.force_field.grid(values,lo,hi,kind=kind,groups=['water'])
    session=app.session.create(scene.build(),'water')
    session.param.set('frames',30).set('fps',60.).set('dt',.001)
    session.param.set('gravity',[0.,0.,0.]).set('air-density',.001 if moving else .1).set('air-friction',1.)
    fixed=session.build()
    started=time.monotonic()
    fixed.start(blocking=True)
    assert fixed.finished()
    poses=np.asarray([fixed.get.vertex(f)[0] for f in range(31)])
    assert np.isfinite(poses).all()
    return {'positions':poses.tolist(), 'solve_seconds':time.monotonic()-started,
            'root':fixed.session.app_root}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--only-moving',action='store_true')
    args=parser.parse_args()
    App.set_backend('cuda')
    results={}
    cases=[('baseline',{}),('zero',{'kind':'air-velocity'}),
           ('velocity',{'kind':'air-velocity','speed':2.}),
           ('stronger',{'kind':'air-velocity','speed':4.}),
           ('reverse',{'kind':'air-velocity','speed':-2.}),
           ('outside',{'kind':'air-velocity','speed':2.,'outside':True}),
           ('acceleration',{'kind':'acceleration','speed':2.}),
           ('spatial',{'kind':'air-velocity','speed':2.,'spatial':True}),
           ('moving_control',{'moving':True}),
           ('moving',{'kind':'air-velocity','speed':2.,'moving':True})]
    if args.only_moving:
        with zipfile.ZipFile(args.output) as z:
            results.update(json.loads(z.read('validation.json'))['cases'])
        cases=[case for case in cases if case[0].startswith('moving')]
    report={'kind':'ClothNeXt.GAIA.Validation','schema':1,'result':'RUNNING','cases':results}
    def save():
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(args.output,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('validation.json',json.dumps(report))
    try:
        for name,options in cases:
            print('RUN',name,flush=True)
            results[name]=run_case(name,**options)
            save()
        data={k:np.asarray(v['positions']) for k,v in results.items()}
        displacement=lambda k:float(data[k][-1,:,0].mean()-data[k][0,:,0].mean())
        np.testing.assert_allclose(data['zero'],data['baseline'],atol=1e-7)
        np.testing.assert_allclose(data['outside'],data['baseline'],atol=1e-7)
        assert displacement('velocity')>0.01
        assert displacement('stronger')>displacement('velocity')
        assert displacement('reverse')<-.01
        velocity=np.diff(data['velocity'].mean(axis=1),axis=0)*60
        assert abs(2-velocity[-1,0])<abs(2-velocity[0,0])
        assert np.ptp(data['spatial'][-1,:,0])>1e-4
        assert np.max(np.abs(data['moving']-data['moving_control']))>1e-4
        report.update(result='PASS',displacements={k:displacement(k) for k in data},
                      initial_velocity=float(velocity[0,0]),final_velocity=float(velocity[-1,0]))
    except Exception as exc:
        report.update(result='FAIL',error=repr(exc))
        raise
    finally: save()


if __name__=='__main__': main()
