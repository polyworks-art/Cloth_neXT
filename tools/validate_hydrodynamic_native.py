"""Isolated native experiment; official frontend only exports synthetic SI scenes.

The explicitly provided experimental executable is launched directly. Nothing is
installed, patched in the official bundle, or copied into the addon.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import numpy as np


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frontend-root',type=Path,required=True)
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    sys.path.insert(0,str(args.frontend_root))
    from frontend import App
    App.set_backend('cuda')  # builder identity only; never used to start a solver
    args.output.mkdir(parents=True,exist_ok=True)
    records=[]
    for name,u,density,dt,divisions,wet in (
        ('dry',(0.2,0,0),1.,.001,1,0.),
        ('normal',(0.2,0,0),1.,.001,1,1.),
        ('normal_half_dt',(0.2,0,0),1.,.0005,1,1.),
        ('normal_refined',(0.2,0,0),1.,.001,2,1.),
        ('heavy',(0.2,0,0),2.,.001,1,1.),
        ('reverse',(-0.2,0,0),1.,.001,1,1.),
        ('parallel',(0,0.2,0),1.,.001,1,1.),
        ('bend_flexible',(0.2,0,0),1.,.0005,2,1.),
        ('bend_stiff',(0.2,0,0),1.,.0005,2,1.),
        ('collider',(0.2,0,0),1.,.0005,1,1.),
        ('animated_collider',(0.2,0,0),1.,.0005,1,1.),
    ):
        app=App.create('cnx-hydro-'+uuid.uuid4().hex,cache_dir=str(args.output/'cache'))
        points=np.array([[0,y,z] for z in np.linspace(0,.1,divisions+1)
                         for y in np.linspace(0,.1,divisions+1)])
        faces=[]
        for j in range(divisions):
            for i in range(divisions):
                a=j*(divisions+1)+i;b=a+1;c=a+divisions+1;d=c+1
                faces.extend(((a,b,c),(b,d,c)))
        app.asset.add.tri('patch',points,np.array(faces))
        scene=app.scene.create('water')
        obj=scene.add('patch')
        for k,v in {'density':density,'young-mod':1000.,'poiss-rat':.3,'bend':.1,
            'deformation-damping':0.,'bending-damping':0.}.items():obj.param.set(k,v)
        if name.startswith('bend_'):
            obj.pin(np.flatnonzero(points[:,1]==0).tolist())
            obj.param.set('bend',.01 if name=='bend_flexible' else 100.)
        if 'collider' in name:
            body=np.array([[.006,y,z] for z in (-.1,.2) for y in (-.1,.2)])
            app.asset.add.tri('body',body,np.array([[0,1,2],[1,3,2]]))
            body_pin=scene.add('body').pin()
            if name=='animated_collider':
                body_pin.move_by([-.002,0,0],t_start=0.,t_end=.1,transition='linear')
        session=app.session.create(scene.build(),'water')
        for k,v in {'frames':4,'fps':40.,'dt':dt,'gravity':[0,0,0],
            'air-density':0.,'disable-contact':False,'cg-tol':1e-5}.items():session.param.set(k,v)
        fixed=session.build()
        config=args.output/(name+'-input.json')
        config.write_text(json.dumps(dict(density_kg_m3=1000.,normal_coefficient=1.,
            tangential_coefficient=0.,wet_area_fraction=wet,velocity_m_s=u)))
        env=os.environ.copy();env['PPF_EXPERIMENTAL_HYDRO_TEST_JSON']=str(config.resolve())
        command=[str(args.solver.resolve()),'--path',fixed.info.path,'--output',fixed.output.path]
        with (args.output/(name+'.log')).open('w') as log:
            run=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        if run.returncode:
            raise RuntimeError(f'{name}: native exit {run.returncode}, see log')
        poses=np.array([fixed.get.vertex(i)[0] for i in range(5)])
        # Fully pinned bodies are exported to the native collision mesh and do
        # not occupy the dynamic vertex output. Verify the patch permutation.
        authored=points
        order=np.argmin(np.linalg.norm(authored[:,None]-poses[0][None],axis=2),axis=1)
        assert len(set(order.tolist()))==len(authored)
        poses=poses[:,order]
        np.save(args.output/(name+'-positions.npy'),poses)
        np.testing.assert_allclose(poses[0,:len(points)],points,atol=1e-7)
        patch_poses=poses[:,:len(points)]
        mean=patch_poses.mean(1)
        measured=(mean[-1]-mean[-2])*40
        # Analytic free translating sheet, averaged over the final output interval.
        k=.5*1000/density*wet
        speed=abs(u[0]);sign=np.sign(u[0]);t0=.075;t1=.1
        expected=0. if k==0 or speed==0 else sign*(speed-np.log((1+k*speed*t1)/(1+k*speed*t0))/(k*(t1-t0)))
        assert np.isfinite(poses).all()
        free=not name.startswith('bend_') and 'collider' not in name
        if free:
            assert abs(measured[0]-expected)<.003,(name,measured,expected)
            assert np.max(np.abs(measured[1:]))<1e-4
        if 'collider' in name:
            body_x=.004 if name=='animated_collider' else .006
            assert np.max(patch_poses[-1,:,0])<body_x
        # Read actual native object contact statistics (num_contact in stdout
        # counts another contact set and omits static collision mesh pairs).
        stats=[]
        addon_root=Path(__file__).resolve().parents[1]
        sys.path.insert(0,str(addon_root))
        from cloth_next.ppf.schema import cbor_codec
        for frame in range(5):
            stats.append(cbor_codec.loads((Path(fixed.output.path)/f'statistics_{frame}.cbor').read_bytes())['payload'])
        contact_peak=max(o['contact_count'] for f in stats for o in f['objects'])
        if 'collider' in name: assert contact_peak>0
        seen={};angles=[]
        triangles=patch_poses[-1,np.asarray(faces)]
        normals=np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
        normals/=np.linalg.norm(normals,axis=1)[:,None]
        for i,face in enumerate(faces):
            for a,b in ((face[0],face[1]),(face[1],face[2]),(face[2],face[0])):
                key=tuple(sorted((a,b)))
                if key in seen: angles.append(np.arccos(np.clip(normals[i]@normals[seen[key]],-1,1)))
                else: seen[key]=i
        records.append(dict(case=name,dt_s=dt,density_kg_m2=density,divisions=divisions,
            measured_final_interval_velocity_m_s=measured.tolist(),analytic_m_s=float(expected) if free else None,
            error_m_s=float(abs(measured[0]-expected)) if free else None,
            max_patch_deflection_m=float(np.max(patch_poses[-1,:,0])),
            mean_hinge_angle_rad=float(np.mean(angles)),native_contact_peak=contact_peak,
            analytic_reference_applicable=free,session=fixed.info.path,
            contact_enabled=True,finite=True))
        print(records[-1],flush=True)
        (args.output/'report.json').write_text(json.dumps(dict(
            scope='Full native CPU Newton simulation, synthetic uniform flow / prescribed wetting; no FLIP adapter',
            solver=str(args.solver.resolve()),solver_sha256=hashlib.sha256(args.solver.read_bytes()).hexdigest(),
            cases=records),indent=2))
    by_name={r['case']:r for r in records}
    assert by_name['normal_half_dt']['error_m_s']<by_name['normal']['error_m_s']
    assert abs(by_name['normal']['measured_final_interval_velocity_m_s'][0]-
               by_name['normal_refined']['measured_final_interval_velocity_m_s'][0])<1e-5
    assert by_name['bend_flexible']['mean_hinge_angle_rad']>by_name['bend_stiff']['mean_hinge_angle_rad']


if __name__=='__main__':main()
