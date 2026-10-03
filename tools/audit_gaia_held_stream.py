# SPDX-License-Identifier: GPL-3.0-or-later
"""Dedicated numerical control using the unchanged official GAIA frontend.

Run with the official package's Python, from its root. Writes only CNX audit
artifacts and ordinary public frontend sessions, never solver source files.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid
import numpy as np
from frontend import App
from frontend._force_field_ import ForceField

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import write_water_container, GaiaContainer
from cloth_next.gaia.water_field import WaterVelocityField, fingerprint, solver_grid
from cloth_next.gaia.streaming import WaterStreamReader
from cloth_next.gaia.held_stream import StreamTarget, drive_held_stream


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resolution',type=int,default=4)
    parser.add_argument('--frames',type=int,default=5)
    parser.add_argument('--dt',type=float,default=.001)
    parser.add_argument('--stream-only',action='store_true',
                        help='Test large held windows without constructing a whole schedule')
    args=parser.parse_args()
    r=args.resolution;n=args.frames
    out=ROOT/'dist/gaia-streaming';out.mkdir(parents=True,exist_ok=True)
    path=out/f'control-{r}-{n}.gaia'
    metadata={'fps':24.,'dimensions':[r,r,max(2,r//2)],
              'field_format':'gaia-0.23','physical_velocity_units':'m/s'}
    metadata['fingerprint']=fingerprint(metadata)
    shape=(max(2,r//2),r,r)
    def fields():
        for i in range(n):
            values=np.zeros((*shape,3),dtype='f4');values[...,0]=1.+i*.2
            yield 120+i,i/24.,WaterVelocityField((-4,-4,-2),(4,4,2),values,np.ones(shape,dtype='f4'))
            del values
    write_water_container(path,metadata,fields())
    App.set_backend('cuda')
    points=np.asarray([[0,y,z] for y in (-.05,0,.05) for z in (-.05,0,.05)])
    tris=[]
    for y in range(2):
        for z in range(2):
            a=y*3+z;tris.extend([[a,a+1,a+4],[a,a+4,a+3]])
    def run(streamed):
        app=App.create('cnx-stream-control-'+uuid.uuid4().hex)
        app.asset.add.tri('cloth',points,np.asarray(tris))
        scene=app.scene.create('water');scene.add('cloth').group('water')
        if not streamed:
            grids=[]
            with GaiaContainer(path) as container:
                for i in range(n):
                    lo,hi,grid=solver_grid(container.frame(i));grids.append(grid)
            scene.force_field.grid(np.stack(grids),lo,hi,times=np.arange(n)/24.,
                                   kind='air-velocity',groups=['water'])
            del grids,grid
        session=app.session.create(scene.build(),'control')
        session.param.set('frames',n-1).set('fps',24.).set('dt',args.dt)
        session.param.set('gravity',[0.,0.,0.]).set('air-density',.1).set('air-friction',1.)
        fixed=session.build();events=[];start=time.perf_counter()
        try:
            if streamed:
                with WaterStreamReader(path,first=120,last=119+n,fps=24.,
                                       expected_fingerprint=metadata['fingerprint']) as reader:
                    drive_held_stream(fixed,[StreamTarget(reader,('water',))],ForceField,
                                      final_boundary=n-1,notify=events.append)
            else:fixed.start(blocking=True)
            assert fixed.finished()
            poses=np.asarray([fixed.get.vertex(i)[0] for i in range(n)])
            assert np.isfinite(poses).all()
            return poses,{'seconds':time.perf_counter()-start,'events':events}
        finally:
            if fixed.is_running():fixed.save_and_quit()
    whole,a=(None,None) if args.stream_only else run(False)
    stream,b=run(True)
    error=None if whole is None else float(np.abs(whole-stream).max())
    report={'resolution':r,'frames':n,'dt':args.dt,'maximum_trajectory_error':error,
            'whole':a,'streamed':b,'window_bytes':2*int(np.prod(shape))*12}
    (out/f'control-{r}-{n}-{args.dt}.json').write_text(json.dumps(report,indent=2))
    if whole is not None:np.testing.assert_allclose(stream,whole,rtol=0,atol=2e-6)
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
