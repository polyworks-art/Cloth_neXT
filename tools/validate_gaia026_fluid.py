# SPDX-License-Identifier: GPL-3.0-or-later
"""Numerical integration gate using the unchanged official 0.26 CUDA worker.

Run with the verified bundle's Python, in its root. Writes only a new test
session and the requested report. No source patches, test-rig overrides or
FLIP source writes. All fields pass through CNX's CBOR adapter and the official
ParamDecoder, including occupancy and coordinate transforms.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import uuid
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.gaia.container import GaiaContainer, write_water_container
from cloth_next.gaia.water_field import WaterVelocityField, fingerprint
from cloth_next.gaia.native_fluid import native_fluid_payload
from cloth_next.ppf.schema import envelope


def main():
    from frontend import App, Utils
    from frontend._decoder_ import ParamDecoder
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    identity=subprocess.run([str(Path.cwd()/'target/cuda/release/ppf-cts-server.exe'),'--version'],
                            capture_output=True,text=True,check=True).stdout
    if '(protocol v0.26, schema v2)' not in identity:
        raise RuntimeError('This integration gate requires the official 0.26 / schema 2 bundle')
    if Utils.busy():
        raise RuntimeError('Another native solve is active')
    App.set_backend('cuda')
    # U, initial solver v, rho, strength, outside; each is an independent group.
    cases=[('moving_water',2.,0.,1.,2.,False),
           ('matched_velocity',2.,2.,1.,2.,False),
           ('opposing_velocity',2.,-2.,1.,2.,False),
           ('still_water',0.,2.,1.,2.,False),
           ('outside',0.,2.,1.,2.,True),
           ('same_location_other_velocity',2.,1.,1.,2.,False),
           ('reverse_region',-2.,0.,1.,2.,False),
           ('stronger_coupling',2.,0.,1.,4.,False),
           ('occupancy_zero',0.,2.,0.,2.,False),
           ('occupied_still_water',0.,-2.,1.,2.,False)]
    app=App.create('cnx-native-fluid-'+uuid.uuid4().hex,cache_dir=str(args.report.parent/'native-cache'))
    scene=app.scene.create('fluid')
    triangles=np.array([[0,1,2],[1,3,2]],dtype=np.int64)
    fields=[]
    with tempfile.TemporaryDirectory(prefix='cnx-fluid-fixture-') as temporary:
        for i,(name,u,v,rho,strength,outside) in enumerate(cases):
            p=np.array([[0,0,0],[0,.1,0],[0,0,.1],[0,.1,.1]],float)
            if outside:p[:,0]+=200
            if name=='reverse_region':p[:,1]+=10
            app.asset.add.tri(name,p,triangles)
            obj=scene.add(name).group('addon-group-'+str(i)).velocity(v,0,0)
            obj.param.set('allow-existing-intersection',True)
            for key,value in {'density':1.,'young-mod':1000.,'poiss-rat':.4,'bend':.83,
                              'force-field-weight':1.,'deformation-damping':0.,'bending-damping':0.}.items():
                obj.param.set(key,value)
            velocity=np.zeros((2,2,2,3),dtype='<f4');velocity[...,0]=u
            field=WaterVelocityField((-100.,)*3,(100.,)*3,velocity,np.full((2,2,2),rho,dtype='<f4'))
            metadata={'provider':{'mode':'FLUID_PARTICLES'},'dimensions':[2,2,2],
                      'velocity_policy':'unclamped',
                      'field_format':'gaia-0.23','physical_velocity_units':'m/s','fps':24.}
            metadata['fingerprint']=fingerprint(metadata)
            path=Path(temporary)/f'{i}.gaia'
            write_water_container(path,metadata,((f,(f-7)/24,field) for f in range(7,32)))
            with GaiaContainer(path) as container:
                fields+=native_fluid_payload(container,[i],first=7,last=31,fps=24.,strength=strength)['fields']
        # One target object, one native volume, opposite spatial regions. This
        # verifies spatial sampling without assigning separate fields per patch.
        patch=np.array([[0,0,0],[0,.1,0],[0,0,.1],[0,.1,.1]],float)
        positions=np.concatenate((patch+[0,0,12],patch+[0,0,-12]))
        app.asset.add.tri('two_regions',positions,np.concatenate((triangles,triangles+4)))
        obj=scene.add('two_regions').group('addon-group-10')
        for key,value in {'density':1.,'young-mod':1000.,'poiss-rat':.4,'bend':.83,
                          'force-field-weight':1.,'deformation-damping':0.,'bending-damping':0.,
                          'allow-existing-intersection':True}.items():obj.param.set(key,value)
        velocity=np.zeros((2,6,2,3),dtype='<f4')
        velocity[:,:3,:,0]=2.;velocity[:,3:,:,0]=-2.
        spatial=WaterVelocityField((-100.,-20.,-100.),(100.,20.,100.),velocity,
                                   np.ones((2,6,2),dtype='<f4'))
        metadata={'provider':{'mode':'FLUID_PARTICLES'},'dimensions':[2,6,2],
                  'velocity_policy':'unclamped','field_format':'velocity-occupancy-v1',
                  'physical_velocity_units':'m/s','fps':24.}
        metadata['fingerprint']=fingerprint(metadata)
        path=Path(temporary)/'spatial.gaia'
        write_water_container(path,metadata,((f,(f-7)/24,spatial) for f in range(7,32)))
        with GaiaContainer(path) as container:
            fields+=native_fluid_payload(container,[10],first=7,last=31,fps=24.,strength=2.)['fields']
        payload=envelope.dumps_envelope(envelope.KIND_PARAM,{'group':[], 'scene':{},
            'force_field':{'fields':fields,'scripts':[]}},schema_version=2)
        # Official decoder sees exactly the serialized bytes emitted by CNX.
        wire_path=Path(temporary)/'param.pickle'
        wire_path.write_bytes(payload)
        ParamDecoder().set_path(str(wire_path)).apply_force_field(scene)
    session=app.session.create(scene.build(),'fluid')
    fps=24.;frames=24
    for key,value in {'frames':frames,'fps':fps,'dt':.001,'gravity':[0,0,0],
                      'air-density':0.,'disable-contact':True,'cg-tol':.0001}.items():
        session.param.set(key,value)
    fixed=session.build();fixed.start(blocking=True)
    if not fixed.finished():raise RuntimeError('Official solver did not finish')
    poses=np.asarray([fixed.get.vertex(i)[0] for i in range(frames+1)])
    if not np.isfinite(poses).all():raise AssertionError('Non-finite solver output')
    results=[]
    for i,(name,u,v,rho,strength,outside) in enumerate(cases):
        # The official map is the authoritative ordering; sequential assets have
        # four distinct vertices, even when positions coincide.
        mean=poses[:,i*4:(i+1)*4,0].mean(axis=1)
        measured=float((mean[-1]-mean[-2])*fps)
        active=0. if outside else rho
        # Independent closed-form reference averaged over the final frame.
        k=strength*active
        expected=v if k==0 else u+(v-u)*(np.exp(-k*(frames-1)/fps)-np.exp(-k*frames/fps))/(k/fps)
        error=abs(measured-expected)
        if error>.025:raise AssertionError(f'{name}: {measured} vs {expected} (error {error})')
        results.append({'case':name,'source_velocity':u,'initial_vertex_velocity':v,
                        'measured_final_velocity':measured,'expected_final_velocity':float(expected),'error':error})
    for region,u in enumerate((2.,-2.)):
        mean=poses[:,40+region*4:44+region*4,0].mean(axis=1)
        measured=float((mean[-1]-mean[-2])*fps)
        expected=u*(1.-(np.exp(-2*(frames-1)/fps)-np.exp(-2*frames/fps))/(2/fps))
        error=abs(measured-expected)
        if error>.025:raise AssertionError(f'spatial region {region}: {measured} vs {expected}')
        results.append({'case':f'spatial_region_{region}','source_velocity':u,
                        'initial_vertex_velocity':0.,'measured_final_velocity':measured,
                        'expected_final_velocity':float(expected),'error':error})
    report={'result':'PASS','protocol':'0.26','schema':'2','backend':'cuda',
            'cases':results,'native_session':str(fixed.session.app_root),
            'scope':'Unmodified native worker, official ParamDecoder, CNX CBOR and sparse bricks; synthetic fluid fixtures'}
    args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
