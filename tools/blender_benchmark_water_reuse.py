import runpy,sys,time,json,hashlib
from pathlib import Path
import argparse
parser=argparse.ArgumentParser(description='Read-only real FLIP shared-field preparation and same-session reuse check')
parser.add_argument('--blend',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
a=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
root=Path(__file__).resolve().parents[1]
out=a.output
out.parent.mkdir(parents=True,exist_ok=True)
if out.exists() or out.with_suffix('.gaia').exists():
    raise ValueError('Choose fresh output paths; existing artifacts are preserved')
sys.argv=[str(root/'tools/blender_profile_preparation.py'),'--','--blend',str(a.blend),'--output',str(out.with_name(out.stem+'-scene-profile.json')),'--frames','1']
g=runpy.run_path(str(root/'tools/blender_profile_preparation.py'))
import bpy
from cloth_next.blender import water_preparation as prep,water_flow
from cloth_next.bake.controller import shared_controller as c
from cloth_next.bake.status import BakeState
objects=tuple(o for o in g['actors'] if o.cloth_next.role=='CLOTH')
settings=objects[0].cloth_next
settings.water_flow_domain=g['domains'][0]
settings.water_flow_resolution='CUSTOM';settings.water_flow_custom_resolution=128
settings.water_flow_container=str(out.with_suffix('.gaia'));settings.water_flow_enabled=True
calls=[];samples=[]
original_worker=prep.WaterPreparationWorker
original_sample=water_flow.FLIPWaterFlowProvider.sample_stream
def worker(*args,**kwargs):
    calls.append(1);return original_worker(*args,**kwargs)
def sample(self,frame):
    samples.append(frame);return original_sample(self,frame)
prep.WaterPreparationWorker=worker;water_flow.FLIPWaterFlowProvider.sample_stream=sample
original_frame=bpy.context.scene.frame_current
measurements=[]
try:
    for repeat in range(2):
        if repeat:
            settings.water_flow_influence=.25
            c.reset();owner=c.transition(BakeState.PREPARING)
            options=dict(job_id=owner.job_id,on_complete=lambda:c.transition(BakeState.PREPARING))
        else:options={}
        before_calls=len(calls);before_samples=len(samples);started=time.perf_counter()
        prep.start(bpy.context,objects,1,3,wait_for_window=False,**options)
        deadline=time.monotonic()+180
        while prep.active():
            assert time.monotonic()<deadline
            prep._pump();time.sleep(.005)
        assert c.snapshot().state in (BakeState.FINISHED,BakeState.PREPARING),c.snapshot()
        measurements.append(dict(seconds=time.perf_counter()-started,workers=len(calls)-before_calls,captures=len(samples)-before_samples))
        if not repeat:cache_sha=hashlib.sha256(out.with_suffix('.gaia').read_bytes()).hexdigest()
    result=dict(result='PASS',objects=[o.name for o in objects],frames=3,measurements=measurements,cache_unchanged=cache_sha==hashlib.sha256(out.with_suffix('.gaia').read_bytes()).hexdigest(),timeline_restored=original_frame==bpy.context.scene.frame_current,headless_gpu_drawing_stub=True)
    assert measurements[0]['workers']==1 and measurements[0]['captures']==3
    assert measurements[1]['workers']==0 and measurements[1]['captures']==0
    assert result['cache_unchanged'] and result['timeline_restored']
    out.write_text(json.dumps(result,indent=2));print(json.dumps(result))
finally:prep.shutdown()
