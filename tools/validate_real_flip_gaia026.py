# SPDX-License-Identifier: GPL-3.0-or-later
"""Official 0.26 worker + real FLIP-derived CNX PARAM, isolated patch and PC2.

Run with the verified bundle's Python from its root, after source validation.
This complements the independent twelve-case physics gate; it does not certify
the production Blender Bake operator or a full 250-frame streaming sequence.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import uuid
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.ppf.coordinates import blender_position_to_ppf,ppf_position_to_blender
from cloth_next.bake.pc2 import write_pc2


def main():
    from frontend import App,Utils
    from frontend._decoder_ import ParamDecoder
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report',type=Path,required=True)
    args=parser.parse_args()
    source=json.loads(args.source_report.read_text())
    if source['result']!='PASS' or not source['source_unchanged']:raise ValueError('Source validation must pass')
    identity=subprocess.run([str(Path.cwd()/'target/cuda/release/ppf-cts-server.exe'),'--version'],
                            capture_output=True,text=True,check=True).stdout
    if '(protocol v0.26, schema v2)' not in identity:raise ValueError('Wrong official protocol/schema')
    if Utils.busy():raise RuntimeError('Another native solve is active')
    App.set_backend('cuda')
    out=args.source_report.parent
    center=np.asarray(source['wet_sample']['position_blender'])
    original=np.asarray([center+offset for offset in ((-.01,0,-.01),(.01,0,-.01),(-.01,0,.01),(.01,0,.01))])
    positions=np.asarray([blender_position_to_ppf(p) for p in original])
    triangles=np.array([[0,1,2],[1,3,2]])
    trajectories=[]
    frames=source['slice'][1]-source['slice'][0]
    for enabled in (False,True):
        app=App.create('cnx-real-flip-'+uuid.uuid4().hex,cache_dir=str(out/'native-cache'))
        scene=app.scene.create('flow')
        app.asset.add.tri('cloth',positions,triangles)
        obj=scene.add('cloth').group('addon-group-0')
        for key,value in {'density':1.,'young-mod':1000.,'poiss-rat':.4,'bend':.83,
                          'force-field-weight':1.,'deformation-damping':0.,'bending-damping':0.}.items():obj.param.set(key,value)
        if enabled:ParamDecoder().set_path(str(out/'param.pickle')).apply_force_field(scene)
        session=app.session.create(scene.build(),'flow')
        for key,value in {'frames':frames,'fps':source['fps'],'dt':.001,'gravity':[0,0,0],
                          'air-density':0.,'disable-contact':True}.items():session.param.set(key,value)
        fixed=session.build();fixed.start(blocking=True)
        if not fixed.finished():raise RuntimeError('Native solve did not finish')
        poses=np.asarray([fixed.get.vertex(i)[0] for i in range(frames+1)])
        trajectories.append(np.asarray([[ppf_position_to_blender(p) for p in frame] for frame in poses]))
    baseline,wet=trajectories
    delta=(wet[-1]-baseline[-1]).mean(axis=0)
    velocity=np.asarray(source['wet_sample']['velocity_blender'])
    alignment=float(np.dot(delta,velocity)/(max(np.linalg.norm(delta)*np.linalg.norm(velocity),1e-30)))
    if not np.isfinite(wet).all() or np.linalg.norm(delta)<1e-5 or alignment<.5:
        raise AssertionError(f'Real fluid did not drive the patch: delta={delta}, alignment={alignment}')
    header=write_pc2(out/'real-flip-cloth.pc2',wet.tolist())
    np.savez(out/'real-flip-trajectories.npz',baseline=baseline,wet=wet,triangles=triangles)
    report={'result':'PASS','protocol':'0.26','schema':'2','backend':'cuda','source_frames':source['slice'],
            'mean_displacement_vs_control':delta.tolist(),'flow_alignment':alignment,
            'source_velocity_blender':velocity.tolist(),'pc2':str(out/'real-flip-cloth.pc2'),
            'pc2_frames':header.frame_count,'scope':'Real FLIP source → .gaia → CNX CBOR → official decoder/worker → PC2; isolated patch'}
    (out/'native-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('REAL_FLIP_NATIVE_PASS '+json.dumps(report),flush=True)


if __name__=='__main__':main()
