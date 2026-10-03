"""Real FLIP resolution comparison through unchanged official GAIA CUDA.

Diagnostic held-field updates measure export and response separately. This does
not enable production streaming or bypass the add-on's schedule memory gate.
"""
import io,json,sys,time,uuid,zipfile
from pathlib import Path
import numpy as np
from frontend import App
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.water_field import solver_grid
from cloth_next.ppf.coordinates import blender_vector_to_ppf
OUT=ROOT/'dist/gaia-resolution'
App.set_backend('cuda')
points=np.asarray([[3.3,-3.1+y,z+dz] for z in (.2,1.3) for y in (-.05,0.,.05) for dz in (-.025,0.,.025)] +
                  [[5.,-3.1+y,.2+dz] for y in (-.05,0.,.05) for dz in (-.025,0.,.025)])
triangles=[]
for offset in (0,9,18):
    for y in range(2):
        for z in range(2):
            a=offset+3*y+z;triangles.extend([[a,a+1,a+4],[a,a+4,a+3]])
triangles=np.asarray(triangles)


def run(resolution=None):
    app=App.create('cnx-resolution-'+str(resolution)+'-'+uuid.uuid4().hex)
    app.asset.add.tri('cloth',np.asarray([blender_vector_to_ppf(v) for v in points]),triangles)
    scene=app.scene.create('water');scene.add('cloth').group('water')
    session=app.session.create(scene.build(),'resolution')
    session.param.set('frames',4).set('fps',60.).set('dt',.001)
    session.param.set('gravity',[0.,0.,0.]).set('air-density',.1).set('air-friction',1.)
    fixed=session.build();start=time.perf_counter();fixed.run_until_frame(0,timeout=120.)
    initialization=time.perf_counter()-start
    upload=0.;encode=0.
    try:
        if resolution is not None:
            with GaiaContainer(OUT/f'field-{resolution}.gaia') as container:field=container.frame(0)
            start=time.perf_counter();lo,hi,values=solver_grid(field);encode=time.perf_counter()-start
            scene.force_field.grid(values,lo,hi,kind='air-velocity',groups=['water'])
            start=time.perf_counter();fixed.update_force_field(scene.force_field);upload=time.perf_counter()-start
            del field,values
        start=time.perf_counter();fixed.step_frame(1,timeout=120.);first_step=time.perf_counter()-start
        start=time.perf_counter();fixed.step_frame(3,timeout=120.);last_steps=time.perf_counter()-start
        assert fixed.finished()
        poses=np.asarray([fixed.get.vertex(i)[0] for i in range(5)])
        assert np.isfinite(poses).all()
        # GAIA X equals Blender X; delta norms are rotation-invariant.
        return poses,{'initialize_seconds':initialization,'axis_encode_seconds':encode,
            'held_field_export_seconds':upload,'reload_and_first_frame_seconds':first_step,
            'remaining_three_frames_seconds':last_steps,'bake_seconds':first_step+last_steps}
    finally:
        if fixed.is_running():fixed.release()


baseline,control=run();report={'control':control,'fixture':points.tolist(),'frames':4,'fps':60,'resolutions':[]}
trajectories={'baseline':baseline}
for resolution in (6,24,100,150,200):
    poses,timings=run(resolution);delta=poses[-1]-baseline[-1]
    entry={'resolution':resolution,**timings,'wet_mean_x':float(delta[:9,0].mean()),
        'dry_in_domain_max_delta':float(np.abs(delta[9:18]).max()),
        'outside_domain_max_delta':float(np.abs(delta[18:]).max())}
    assert entry['outside_domain_max_delta']<1e-6
    if resolution>=100:
        assert entry['wet_mean_x']>1e-4 and entry['dry_in_domain_max_delta']<1e-6
    trajectories[str(resolution)]=poses;report['resolutions'].append(entry)
    print(json.dumps(entry),flush=True)
path=OUT/'solver-comparison.gaia'
with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('audit/solver.json',json.dumps(report,indent=2))
    buf=io.BytesIO();np.savez_compressed(buf,**trajectories);archive.writestr('audit/trajectories.npz',buf.getvalue())
(OUT/'solver-comparison.json').write_text(json.dumps(report,indent=2))
