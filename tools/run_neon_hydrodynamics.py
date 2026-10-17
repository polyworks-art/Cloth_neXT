"""Actual native NEON Dry/Flow/Hydro replays; preserve captured original physics.

Official frontend is an exporter only. The supplied isolated native executable
is the sole simulation process. Experimental surface transport stays external
to official PARAM and existing .gaia source caches.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.ppf.schema import envelope
from cloth_next.ppf.official_scene_worker import apply_material_tables
from cloth_next.gaia.native_fluid import native_fluid_payload_file
from cloth_next.gaia.container import GaiaContainer
from tools.hydrodynamic_export_lock import export_lock


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--frontend-root',type=Path,required=True)
    p.add_argument('--solver',type=Path,required=True)
    p.add_argument('--volumes',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--frames',type=int,default=30)
    p.add_argument('--cases',nargs='+',default=['dry1','flow1','hydro1','dry2','hydro2'])
    p.add_argument('--timeout',type=int,default=3600)
    p.add_argument('--tight-newton',action='store_true')
    p.add_argument('--precond',choices=('block-jacobi','schwarz','multigrid'))
    a=p.parse_args();sys.path.insert(0,str(a.frontend_root));from frontend import App,BlenderApp
    App.set_backend('cuda');a.output.mkdir(parents=True,exist_ok=True)
    cache=Path('F:/16_Cloth_neXt/cache_AD')
    source=cache/'.cloth_next_export/scene/00/006a0ef0ba8466634b590c5dd7bb9f2a7e7295e2ecc7438d129afa5beebf175c'
    plan=json.loads((source/'metadata.json').read_text())['plan']
    data=source/'scene.cbor'
    base=envelope.loads_envelope((cache/'.cloth_next_export/param/c3/c3f3dacd959b78c10104af0d1c25c29bfecbb4b753d6345fa5e7c0bbd1cc928d/param.cbor').read_bytes(),envelope.KIND_PARAM,schema_version=2)
    base['scene']['frames']=a.frames-1
    if a.tight_newton:
        base['scene'].update({'min-newton-steps':4,'max-dx':.001,'cg-tol':.0001})
    if a.precond:
        base['scene']['precond']=a.precond
    saved=json.loads((ROOT/'docs/validation/gaia026/coupling-investigation/saved-settings.json').read_text())
    objects={o['name']:o for part in envelope.loads_envelope(data.read_bytes(),envelope.KIND_SCENE,schema_version=2) for o in part['object']}
    zones=[]
    for obj in saved:
        if obj.get('face_material_params'):
            s=objects[obj['name']]
            zones.append(dict(uuid=s['uuid'],faces=s['face'],params=obj['face_material_params']))
    water=cache/'S04_NEON_Garments_002_WaterFlow.gaia'
    with GaiaContainer(water) as c:fingerprint=c.manifest['water']['fingerprint']
    descriptor=dict(version=1,field_kind='native-file',first=1,last=a.frames,fps=30.,solver_fps=30.,
        targets=[dict(path=str(water),fingerprint=fingerprint,groups=[0,1,2],influence=1.,strength=1.,flow=1.)])
    report=dict(source_scene=str(data),source_scene_sha256=hashlib.sha256(data.read_bytes()).hexdigest(),
        solver=str(a.solver.resolve()),solver_sha256=hashlib.sha256(a.solver.read_bytes()).hexdigest(),
        backend=subprocess.check_output([str(a.solver.resolve()),'--backend'],text=True).strip(),
        frames=a.frames,quality=base['scene'],saved_zones=True,cases=[])
    for label in a.cases:
        mode=label.rstrip('0123456789')
        if mode not in ('dry','flow','hydro'):raise ValueError('Unknown case')
        folder=a.output/label;folder.mkdir(exist_ok=False)
        payload=envelope.dumps_envelope(envelope.KIND_PARAM,copy.deepcopy(base),schema_version=2)
        app=BlenderApp('cnx_spatial_neon_'+label+'_'+str(time.time_ns()))
        project=Path(app._root)
        shutil.copyfile(data,project/'data.pickle')
        if mode=='flow':native_fluid_payload_file(payload,descriptor,project/'param.pickle')
        else:(project/'param.pickle').write_bytes(payload)
        print(label,'EXPORT',project,flush=True)
        with export_lock():app.populate().make()
        fixed=app._fixed_session
        session=project/'session'
        mapping=envelope.loads_envelope((session/'map.pickle').read_bytes(),envelope.KIND_VERTEX_MAP,schema_version=2)
        apply_material_tables(session/'bin',mapping,zones)
        env=os.environ.copy()
        env.pop('PPF_EXPERIMENTAL_HYDRO_TEST_JSON',None);env.pop('PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR',None)
        if mode=='hydro':
            config=folder/'input.json';config.write_text(json.dumps(dict(density_kg_m3=1000.,normal_coefficient=1.,
                tangential_coefficient=0.,wet_area_fraction=1.,velocity_m_s=[0,0,0],volume_mode=1)))
            env['PPF_EXPERIMENTAL_HYDRO_TEST_JSON']=str(config.resolve())
            env['PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR']=str(a.volumes.resolve())
            if (session/'bin/force_field').exists():raise ValueError('Hydrodynamics must not also apply old Fluid Flow')
        started=time.monotonic();print(label,'NATIVE SOLVE',flush=True)
        with (folder/'native.log').open('w') as log:
            run=subprocess.run([str(a.solver.resolve()),'--path',str(session),'--output',str(session/'output')],
                env=env,stdout=log,stderr=subprocess.STDOUT,timeout=a.timeout)
        if run.returncode:raise RuntimeError(f'{label}: native exit {run.returncode}; see {folder}/native.log')
        if not (session/'output/finished.txt').exists():raise RuntimeError('Native solver did not finish')
        poses=np.asarray([fixed.get.vertex(i)[0] for i in range(a.frames)])
        if not np.isfinite(poses).all():raise ArithmeticError('Nonfinite native positions')
        np.save(folder/'native-positions.npy',poses)
        shutil.copyfile(session/'map.pickle',folder/'map.cbor')
        for t in plan['targets']:
            np.save(folder/(t['name']+'.npy'),poses[:,mapping[t['uuid']]])
        record=dict(label=label,mode=mode,session=str(session),seconds=time.monotonic()-started,
            shape=list(poses.shape),finite=True,groups=base['group'])
        report['cases'].append(record);(a.output/'report.json').write_text(json.dumps(report,indent=2))
        print('FINISHED',record['label'],record['seconds'],flush=True)


if __name__=='__main__':main()
