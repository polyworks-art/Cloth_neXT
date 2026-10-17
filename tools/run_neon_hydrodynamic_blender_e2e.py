"""Launch isolated regular Blender Bake tests; never change the user's registry."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--dev',type=Path,required=True)
    p.add_argument('--official',type=Path,required=True)
    p.add_argument('--blender',type=Path,required=True)
    p.add_argument('--label',required=True)
    p.add_argument('--last',type=int,default=133)
    p.add_argument('--first',type=int,default=130)
    p.add_argument('--worker',type=Path)
    p.add_argument('--backend',type=Path)
    p.add_argument('--preparer',type=Path,help='Explicit isolated experimental preparation entry point')
    p.add_argument('--dry',action='store_true')
    a=p.parse_args()
    # Child processes use the isolated runtime as cwd. Never hand them paths
    # whose meaning depends on the launcher's current directory.
    for name in ('root','dev','official','blender','worker','backend','preparer'):
        value=getattr(a,name)
        if value is not None:setattr(a,name,value.resolve())
    runtime=a.root/'runtime';profile=a.root/'profile'
    target=runtime/'target/cuda/release'
    shutil.copy2(a.worker or a.dev/'target/hydrodynamics-cuda/release/ppf-contact-solver.exe',target/'ppf-contact-solver.exe')
    shutil.copy2(a.backend or a.dev/'crates/ppf-cts-compute/cuda/build/lib/libsimbackend_cuda.dll',runtime/'bin/libsimbackend_cuda.dll')
    # The existing backend selector expects the complete CPU/CUDA layout.
    cpu=runtime/'target/cpu/release'
    shutil.copytree(a.official/'target/cpu/release',cpu,dirs_exist_ok=True)
    config={'kind':'ClothNeXt.IsolatedHydrodynamics.v1','runtime':str(runtime),
        'server':str(target/'ppf-cts-server.exe'),'worker':str(target/'ppf-contact-solver.exe'),
        'backend':str(runtime/'bin/libsimbackend_cuda.dll'),
        'python':str(runtime/'python/python.exe'),'dependencies':str(a.dev/'.tools/python-deps'),
        'preparer':str(a.preparer or ROOT/'tools/prepare_neon_hydrodynamics.py')}
    for role in ('server','worker','backend'):
        with Path(config[role]).open('rb') as stream:
            config[role+'_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
    manifest=profile/'hydrodynamics.json';manifest.write_text(json.dumps(config,indent=2))
    registry={'registry_version':1,'selected_installation_id':'experimental-hydro-cuda',
        'installations':[{'installation_id':'experimental-hydro-cuda','display_name':'Isolated Hydrodynamics CUDA',
            'source':'external','root_path':str(runtime),'executable_path':config['server'],
            'frontend_path':str(runtime/'frontend'),'package_version':'0.1.0','protocol_version':'0.26',
            'schema_version':'2','official_release_tag':None,'managed':False,'verified':True,'healthy':True}]}
    registry_path=profile/'localappdata/ClothNeXt/solver/registry.json'
    registry_path.parent.mkdir(parents=True,exist_ok=True)
    registry_path.write_text(json.dumps(registry,indent=2))
    env=os.environ.copy()
    temporary=a.root/'temp';temporary.mkdir(exist_ok=True)
    env.update(BLENDER_USER_RESOURCES=str(profile),LOCALAPPDATA=str(profile/'localappdata'),
        CLOTH_NEXT_EXPERIMENTAL_HYDRO_MANIFEST=str(manifest),PYTHONDONTWRITEBYTECODE='1',
        CARGO_TARGET_DIR=str(runtime/'target/cuda'),MPLCONFIGDIR=str(profile/'matplotlib'),
        TEMP=str(temporary),TMP=str(temporary))
    env['PATH']=str(runtime/'bin')+os.pathsep+env.get('PATH','')
    output=a.root/a.label
    command=[str(a.blender),'--factory-startup','--background','--python',
        str(ROOT/'tools/blender_neon_hydrodynamic_e2e.py'),'--','--repo',str(ROOT),
        '--blend',str(a.root/'SW04_NEON-copy.blend'),'--output',str(output),'--first',str(a.first),'--last',str(a.last)]
    if a.dry:command.append('--dry')
    with (a.root/(a.label+'.log')).open('w',encoding='utf-8') as log:
        result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT)
    report=json.loads((output/'report.json').read_text())
    print(json.dumps(report,indent=2))
    return 0 if result.returncode==0 and report['result']=='PASS' else 1


if __name__=='__main__':sys.exit(main())
