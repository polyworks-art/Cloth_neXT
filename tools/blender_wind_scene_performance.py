"""Benchmark a saved scene copy without touching the open user session."""
import argparse, sys, time, json, cProfile, pstats
from pathlib import Path
from types import SimpleNamespace
import bpy
args=argparse.ArgumentParser()
args.add_argument('--scene',required=True); args.add_argument('--source',required=True)
args.add_argument('--output',required=True); args.add_argument('--frames',type=int,default=5)
opts=args.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,opts.source)
from cloth_next.blender import registration, wind_fields
from cloth_next.ppf.schema import envelope
registration.register()
try:
    bpy.ops.wm.open_mainfile(filepath=opts.scene)
    scene=bpy.context.scene
    forces=list(wind_fields.force_objects(bpy.context))
    result={'objects':len(scene.objects),'forces':len(forces),'source':opts.source,'modes':{}}
    payload=envelope.dumps_envelope(envelope.KIND_PARAM,{'scene':{'air-density':.1},
        'force_field':{'grids':[],'scripts':[]}},schema_version=2)
    for mode in ('COLLIDERS','SCENE'):
        for obj in forces: obj.cloth_next.force.wind_obstacles=mode
        scene.frame_set(1)
        wind_fields._invalidate_obstacles()
        start=time.perf_counter(); vertices=wind_fields.preview(bpy.context); cold=time.perf_counter()-start
        start=time.perf_counter()
        for _ in range(10): wind_fields.preview(bpy.context)
        redraw=(time.perf_counter()-start)/10
        timeline=[]
        profiler=cProfile.Profile(); profiler.enable()
        for frame in range(2,opts.frames+2):
            start=time.perf_counter(); scene.frame_set(frame); wind_fields.preview(bpy.context)
            timeline.append(time.perf_counter()-start)
        profiler.disable()
        with open(opts.output+'.'+mode+'.profile.txt','w') as stream:
            pstats.Stats(profiler,stream=stream).sort_stats('cumtime').print_stats(25)
        start=time.perf_counter()
        encoded,digest=wind_fields.apply_wind_payload(bpy.context,payload,SimpleNamespace(protocol_version='0.23'),1,opts.frames)
        export=time.perf_counter()-start
        result['modes'][mode]={'cold':cold,'redraw':redraw,'timeline':timeline,'export':export,'vertices':len(vertices),'digest':digest}
        Path(opts.output).write_text(json.dumps(result,indent=2))
        print('WIND_SCENE_PERFORMANCE '+json.dumps(result['modes'][mode]),flush=True)
finally: registration.unregister()
