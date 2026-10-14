# SPDX-License-Identifier: GPL-3.0-or-later
"""Run in background Blender: read a real FLIP bake and create an owned .gaia slice.

Never save the source .blend, rebake FLIP, change its export settings or write
its cache. Every requested source frame is read and validated; only a bounded
test slice is reconstructed. All settings for the slice are recorded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import bpy
import addon_utils
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blend',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--first',type=int,default=150)
    parser.add_argument('--last',type=int,default=152)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    args.output.mkdir(parents=True,exist_ok=True)
    if any(args.output.iterdir()):raise ValueError('Use a fresh test output directory')
    before=hashlib.sha256(args.blend.read_bytes()).hexdigest()
    repos=bpy.context.preferences.extensions.repos
    if not any(r.module=='superhivemarket_com' for r in repos):
        repos.new(name='FLIP source inspection',module='superhivemarket_com')
    module=addon_utils.enable('bl_ext.superhivemarket_com.flip_fluids_addon',default_set=True)
    if module is None:raise RuntimeError('Installed FLIP Fluids could not register')
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()))
    from cloth_next.blender.water_flow import FLIPWaterFlowProvider
    from cloth_next.gaia.water_field import grid_plan,reconstruct,fingerprint
    from cloth_next.gaia.container import write_water_container,GaiaContainer
    from cloth_next.gaia.native_fluid import native_fluid_payload
    from cloth_next.ppf.schema import envelope
    domains=[o for o in bpy.context.scene.objects
             if getattr(getattr(o,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN']
    if len(domains)!=1:raise ValueError('The test scene must have exactly one FLIP domain')
    scene=bpy.context.scene
    first,last=scene.frame_start,scene.frame_end
    if not first<=args.first<=args.last<=last:raise ValueError('Slice outside the source scene range')
    provider=FLIPWaterFlowProvider(bpy.context,domains[0],range(first,last+1))
    if provider.mode!='FLUID_PARTICLES':raise ValueError('Liquid-particle velocities are required')
    stamps={p:(p.stat().st_size,p.stat().st_mtime_ns) for p in provider.source_paths}
    counts=[]
    for frame in range(first,last+1):
        positions,velocities=provider.sample(frame)
        if not np.isfinite(positions).all() or not np.isfinite(velocities).all():
            raise ValueError(f'Non-finite source data at frame {frame}')
        counts.append(len(positions))
        del positions,velocities
        if frame%25==0:print(f'FLIP_FRAME_VALIDATED {frame}/{last}',flush=True)
    fps=scene.render.fps/scene.render.fps_base
    layout=grid_plan(provider.minimum,provider.maximum,'100')
    metadata={'provider':provider.identity,'frames':[args.first,args.last],'fps':fps,
              'dimensions':layout['dimensions'],'algorithm':'trilinear-splat-support-v2',
              'field_format':'velocity-occupancy-v1','physical_velocity_units':'m/s',
              'velocity_policy':'unclamped','grid_layout':layout}
    metadata['fingerprint']=fingerprint(metadata)
    diagnostics=[]
    def frames():
        for frame in range(args.first,args.last+1):
            p,v=provider.sample(frame)
            f=reconstruct(p,v,provider.minimum,provider.maximum,layout['dimensions'],
                          max_speed=None,support_threshold=layout['support_threshold'])
            diagnostics.append({'frame':frame,'particle_count':len(p),
                'occupied_nodes':int(np.count_nonzero(f.influence)),
                'max_source_speed':float(np.linalg.norm(v,axis=1).max())})
            del p,v
            yield frame,(frame-args.first)/fps,f
    path=args.output/'Flip_Test.gaia'
    write_water_container(path,metadata,frames())
    with GaiaContainer(path) as c:
        ff=native_fluid_payload(c,[0],first=args.first,last=args.last,fps=fps,strength=2.)
        f=c.frame(0)
        score=np.linalg.norm(f.velocity,axis=-1)*f.influence
        z,y,x=np.unravel_index(np.argmax(score),score.shape)
        position=np.asarray(f.minimum)+(np.asarray(f.maximum)-f.minimum)*np.asarray((x,y,z))/(np.asarray(layout['dimensions'])-1)
        wet={'position_blender':position.tolist(),'velocity_blender':f.velocity[z,y,x].tolist(),
             'occupancy':float(f.influence[z,y,x])}
    payload=envelope.dumps_envelope(envelope.KIND_PARAM,{'group':[],'scene':{},'force_field':ff},schema_version=2)
    (args.output/'param.pickle').write_bytes(payload)
    for p,stamp in stamps.items():
        if (p.stat().st_size,p.stat().st_mtime_ns)!=stamp:raise ValueError('Source cache changed during validation')
    if hashlib.sha256(args.blend.read_bytes()).hexdigest()!=before:raise ValueError('Source .blend changed during validation')
    report={'result':'PASS','blend':str(args.blend),'blend_sha256':before,'source_frames':[first,last],
            'validated_frames':len(counts),'particle_count_min':min(counts),'particle_count_max':max(counts),
            'source_identity':provider.identity,'slice':[args.first,args.last],'fps':fps,
            'grid_dimensions':layout['dimensions'],'gaia':str(path),'payload_bytes':len(payload),
            'wet_sample':wet,'frame_diagnostics':diagnostics,'source_unchanged':True}
    (args.output/'source-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('REAL_FLIP_SOURCE_PASS '+json.dumps({k:v for k,v in report.items() if k!='source_identity'}),flush=True)


if __name__=='__main__':main()
