"""Real user's exported cloth + baked FLIP field, unchanged official CUDA."""
import argparse
import io
import json
from pathlib import Path
import sys
import time
import uuid
import zipfile
import numpy as np
from frontend import App
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.water_field import solver_grid
from cloth_next.ppf.coordinates import blender_vector_to_ppf


def run(source, enabled, offset):
    with zipfile.ZipFile(source) as z:
        fixture=np.load(io.BytesIO(z.read('fixture/Plane.npz')))
        p,tri=fixture['positions'],fixture['triangles']
    # Axis conversion only; exactly the exported original cloth topology.
    positions=np.asarray([blender_vector_to_ppf(v+offset) for v in p])
    app=App.create('cnx-real-water-'+uuid.uuid4().hex)
    app.asset.add.tri('cloth',positions,tri)
    scene=app.scene.create('water')
    scene.add('cloth').group('water')
    if enabled:
        with GaiaContainer(source) as c:
            fields=[solver_grid(c.frame(i)) for i in range(149,154)]
            lo,hi=fields[0][:2]
            values=np.asarray([item[2] for item in fields])
        scene.force_field.grid(values,lo,hi,times=[i/24 for i in range(5)],kind='air-velocity',groups=['water'])
    session=app.session.create(scene.build(),'water')
    session.param.set('frames',4).set('fps',24.).set('dt',.002)
    session.param.set('gravity',[0,0,0]).set('air-density',.1).set('air-friction',1.)
    fixed=session.build()
    started=time.monotonic()
    fixed.start(blocking=True)
    assert fixed.finished()
    poses=np.asarray([fixed.get.vertex(i)[0] for i in range(5)])
    assert np.isfinite(poses).all()
    return poses, {'seconds':time.monotonic()-started,'root':fixed.session.app_root}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--offset',type=float,nargs=3,default=[0,0,0])
    args=parser.parse_args()
    App.set_backend('cuda')
    offset=np.asarray(args.offset)
    baseline,a=run(args.source,False,offset)
    water,b=run(args.source,True,offset)
    delta=water-baseline
    mean=float(delta[-1,:,0].mean())
    peak=float(delta[-1,:,0].max())
    passed=mean>1e-5 and peak>1e-4
    report={'result':'PASS' if passed else 'NO_FLOW_RESPONSE',
            'scope':'original cloth topology; source FLIP frames 150-154; 4 solver steps, no collider/pins',
            'authored_test_offset_before_simulation':offset.tolist(),
            'mean_x_displacement_vs_control':mean,'maximum_x_displacement_vs_control':peak,
            'baseline':a,'water':b}
    with zipfile.ZipFile(args.source,'a',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('actual-cuda-validation.json',json.dumps(report))
        data=io.BytesIO()
        np.savez_compressed(data,baseline=baseline,water=water)
        z.writestr('validation/actual-trajectories.npz',data.getvalue())
    print(json.dumps(report),flush=True)
    assert passed, 'No flow response: check fixture placement against liquid support'


if __name__=='__main__': main()
