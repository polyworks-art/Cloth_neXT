"""Small complete native solves through the versioned experimental transport."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.gaia.hydrodynamic_transport import ExperimentalHydroWriter,dense_bricks


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frontend-root',type=Path,required=True)
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cases',nargs='+',help='Run a selected subset for an initial integration check')
    p.add_argument('--self-contact-inertia-control',action='store_true',
                   help='Two-layer contact control: moving cloth, fixed opposing mesh, zero hydro coefficients')
    a=p.parse_args();sys.path.insert(0,str(a.frontend_root));from frontend import App
    if a.self_contact_inertia_control and a.cases!=['self_collision']:
        raise ValueError('Inertial control requires --cases self_collision')
    output_frames=200 if a.self_contact_inertia_control else 4
    output_fps=2000. if a.self_contact_inertia_control else 40.
    backend=subprocess.check_output([str(a.solver.resolve()),'--backend'],text=True).strip()
    App.set_backend('cuda');a.output.mkdir(parents=True,exist_ok=True)
    records=[]
    for case in ('uniform','uniform_one_brick','still_both_sides','dry','partial','gradient','temporal',
                 'reverse','moving_cloth','outside','heavy','refined','half_dt','bend_flexible',
                 'bend_stiff','collider','animated_collider','self_collision','surface_moving','tangential'):
        if a.cases and case not in a.cases:continue
        folder=a.output/case;folder.mkdir(exist_ok=False)
        app=App.create('cnx-hydro-spatial-'+uuid.uuid4().hex,cache_dir=str(folder/'cache'))
        divisions=2 if case in ('refined','bend_flexible','bend_stiff') else 1
        points=np.array([[0,y,z] for z in np.linspace(0,.1,divisions+1) for y in np.linspace(0,.1,divisions+1)])
        if case=='outside':points[:,0]+=3
        faces=[]
        for j in range(divisions):
            for i in range(divisions):
                q=j*(divisions+1)+i
                faces.extend(((q,q+1,q+divisions+1),(q+1,q+divisions+2,q+divisions+1)))
        faces=np.asarray(faces)
        if case=='self_collision':
            points=np.concatenate((points,points+[.006,0,0]));faces=np.concatenate((faces,faces+4))
        if a.self_contact_inertia_control:
            app.asset.add.tri('patch',points[:4],faces[:2])
            app.asset.add.tri('pinned_layer',points[4:],faces[2:]-4)
        else:app.asset.add.tri('patch',points,faces)
        scene=app.scene.create('spatial');obj=scene.add('patch')
        if case=='moving_cloth' or a.self_contact_inertia_control:obj.velocity(.2,0,0)
        for k,v in {'density':2. if case=='heavy' else 1.,'young-mod':1000.,'poiss-rat':.3,'bend':.1,
                    'deformation-damping':0.,'bending-damping':0.}.items():obj.param.set(k,v)
        if case.startswith('bend_'):
            obj.pin(np.flatnonzero(points[:,1]==0).tolist())
            obj.param.set('bend',.01 if case=='bend_flexible' else 100.)
        if case=='self_collision':
            if a.self_contact_inertia_control:
                layer=scene.add('pinned_layer');layer.pin()
                for k,v in {'density':1.,'young-mod':1000.,'poiss-rat':.3,'bend':.1,
                            'deformation-damping':0.,'bending-damping':0.}.items():layer.param.set(k,v)
            else:obj.pin([4,5,6,7])
        if case in ('collider','animated_collider'):
            body=np.array([[.006,y,z] for z in (-.1,.2) for y in (-.1,.2)])
            app.asset.add.tri('body',body,np.array([[0,1,2],[1,3,2]]))
            pin=scene.add('body').pin()
            if case=='animated_collider':pin.move_by([-.002,0,0],t_start=0.,t_end=.1,transition='linear')
        # A fully pinned separate object is exported as a static collider;
        # SessionGet.vertex contains only the dynamic patch in this control.
        if a.self_contact_inertia_control:points=points[:4];faces=faces[:2]
        session=app.session.create(scene.build(),'spatial')
        for k,v in {'frames':output_frames,'fps':output_fps,'dt':.00025 if case=='half_dt' else .0005,'gravity':[0,0,0],
                    'air-density':0.,'disable-contact':False,'cg-tol':1e-5,
                    'min-newton-steps':4,'max-dx':.0001}.items():session.param.set(k,v)
        fixed=session.build()
        writer=ExperimentalHydroWriter(folder/'volumes',start=0,step=.1,
            provenance=dict(kind='analytic test field',case=case))
        n=8 if case=='uniform_one_brick' else 16
        cell=np.full(3,2/n);corner=np.full(3,-1.)
        xyz=np.meshgrid(*(corner[j]+(np.arange(n)+.5)*cell[j] for j in (2,1,0)),indexing='ij')
        # Adaptive contact steps can pass the last output time; the control
        # needs a constant-field lookahead, as the NEON transport already has.
        for frame in range(3 if a.self_contact_inertia_control else 2):
            vel=np.zeros((n,n,n,4),dtype='<f4');vel[...,0]=.2;vel[...,3]=1
            if case=='gradient':vel[...,0]+=.5*xyz[1]
            if case=='temporal':vel[...,0]=.2*(1-2*frame)
            if case=='reverse':vel[...,0]=-.2
            if case=='still_both_sides':vel[...,0]=0
            if case=='tangential':vel[...,0]=0;vel[...,1]=.2
            sdf=np.zeros_like(vel);sdf[...,0]=-.2;sdf[...,3]=1
            if case=='dry':sdf[...,0]=.2
            if case=='partial':sdf[...,0]=xyz[1]-.05
            if case=='surface_moving':sdf[...,0]=xyz[1]-.05-.05*frame
            for role,value in ((0,vel),(1,sdf)):
                t,b,res=dense_bricks(value);writer.append(role,t,b,res,corner,np.eye(3),cell)
        writer.finish()
        config=folder/'input.json';config.write_text(json.dumps(dict(density_kg_m3=1000,
            normal_coefficient=0 if a.self_contact_inertia_control else 1,
            tangential_coefficient=.02 if case=='tangential' else 0,wet_area_fraction=1,
            velocity_m_s=[0,0,0],volume_mode=1)))
        env=os.environ.copy();env['PPF_EXPERIMENTAL_HYDRO_TEST_JSON']=str(config.resolve())
        env['PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR']=str((folder/'volumes').resolve())
        with (folder/'native.log').open('w') as log:
            run=subprocess.run([str(a.solver.resolve()),'--path',fixed.info.path,'--output',fixed.output.path],
                env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        if run.returncode:raise RuntimeError(f'{case}: native failed {run.returncode}; see {folder}/native.log')
        poses=np.array([fixed.get.vertex(i)[0] for i in range(output_frames+1)])
        order=np.argmin(np.linalg.norm(points[:,None]-poses[0][None],axis=2),axis=1)
        assert len(set(order.tolist()))==len(points)
        poses=poses[:,order]
        np.save(folder/'positions.npy',poses)
        telemetry=np.fromfile(Path(fixed.output.path)/f'hydro_{output_frames}.bin',dtype='<f4').reshape(-1,16)
        np.save(folder/'native-telemetry.npy',telemetry)
        velocity=(poses[-1].mean(0)-poses[-2].mean(0))*output_fps
        assert np.isfinite(poses).all() and np.isfinite(telemetry).all()
        if case in ('dry','outside','still_both_sides'):assert np.max(np.abs(poses-poses[0]))<1e-6
        if case=='moving_cloth':assert abs(velocity[0]-.2)<1e-5
        if case=='uniform':assert abs(velocity[0]-.17937367)<.002
        if case=='reverse':assert velocity[0]<0
        if case=='partial':assert 0<telemetry[:,1].sum()<telemetry[:,0].sum()
        if case in ('collider','animated_collider'):
            assert poses[-1,:,0].max()<(.004 if case=='animated_collider' else .006)
        if case=='self_collision':
            if a.self_contact_inertia_control:assert poses[-1,:,0].max()<.006
            else:assert poses[-1,:4,0].max()<poses[-1,4:,0].min()
        from cloth_next.ppf.schema import cbor_codec
        stats=[cbor_codec.loads((Path(fixed.output.path)/f'statistics_{i}.cbor').read_bytes())['payload'] for i in range(output_frames+1)]
        contact_peak=max(o['contact_count'] for f in stats for o in f['objects'])
        if case in ('collider','animated_collider','self_collision'):assert contact_peak>0
        tri=poses[-1,faces];normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
        normals/=np.linalg.norm(normals,axis=1)[:,None];edges={};angles=[]
        for j,f in enumerate(faces):
            for v0,v1 in ((f[0],f[1]),(f[1],f[2]),(f[2],f[0])):
                key=tuple(sorted((v0,v1)))
                if key in edges:angles.append(np.arccos(np.clip(normals[j]@normals[edges[key]],-1,1)))
                else:edges[key]=j
        if case=='temporal':
            middle=np.fromfile(Path(fixed.output.path)/'hydro_2.bin',dtype='<f4').reshape(-1,16)
            assert middle[:,13].max()<1e-5
        records.append(dict(case=case,final_velocity_m_s=velocity.tolist(),
            wet_area_m2=float(telemetry[:,1].sum()),normal_load_n=float(telemetry[:,2].sum()),
            force_n=telemetry[:,4:7].sum(0).tolist(),two_sided_wet_area_m2=float(telemetry[:,11].sum()),
            fluid_speed_max_m_s=float(telemetry[:,13].max()),session=fixed.info.path))
        records[-1].update(native_contact_peak=contact_peak,mean_hinge_angle_rad=float(np.mean(angles)))
        print(records[-1],flush=True)
        (a.output/'report.json').write_text(json.dumps(dict(cases=records,backend=backend,
            output_frames=output_frames+1,output_fps=output_fps,
            reference_variant='two-layer inertial contact without hydro load' if a.self_contact_inertia_control else 'standard',
            transport='typed velocity/SDF v1',host_forces=False),indent=2))
    if a.cases:
        assert set(a.cases)=={r['case'] for r in records},'Unknown or missing case'
        report=json.loads((a.output/'report.json').read_text());report['result']='PASS'
        (a.output/'report.json').write_text(json.dumps(report,indent=2))
        return
    by_name={r['case']:r for r in records}
    np.testing.assert_allclose(by_name['uniform']['final_velocity_m_s'],
                               by_name['uniform_one_brick']['final_velocity_m_s'],atol=1e-5)
    np.testing.assert_allclose(by_name['uniform']['final_velocity_m_s'],by_name['refined']['final_velocity_m_s'],atol=1e-5)
    assert by_name['heavy']['final_velocity_m_s'][0]<by_name['uniform']['final_velocity_m_s'][0]
    assert abs(by_name['half_dt']['final_velocity_m_s'][0]-.17937367)<abs(by_name['uniform']['final_velocity_m_s'][0]-.17937367)
    assert by_name['bend_flexible']['mean_hinge_angle_rad']>by_name['bend_stiff']['mean_hinge_angle_rad']
    report=json.loads((a.output/'report.json').read_text());report['result']='PASS'
    (a.output/'report.json').write_text(json.dumps(report,indent=2))


if __name__=='__main__':main()
