"""Read-only real-FLIP check of main-thread capture, async conversion, reuse and Cancel."""
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time
import bpy

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from cloth_next.blender import water_flow, water_preparation
from cloth_next.bake.controller import shared_controller
from cloth_next.bake.status import BakeState

original_frame = bpy.context.scene.frame_current
original_subframe = bpy.context.scene.frame_subframe
output = root/'dist'/'water-preparation-real.gaia'
settings = SimpleNamespace(water_flow_enabled=True, enabled=True, role='CLOTH',
    water_flow_domain=bpy.context.scene.objects['FLIP Domain'], water_flow_influence=1.,
    water_flow_velocity_scale=1., water_flow_resolution='AUTO', water_flow_container=str(output),
    cache_directory='')
obj = SimpleNamespace(name='Water Preparation Smoke', cloth_next=settings)
captures = []
original_sample = water_flow.FLIPWaterFlowProvider.sample
main_thread = threading.get_ident()

def sample(self, frame):
    assert threading.get_ident() == main_thread
    started = time.monotonic()
    result = original_sample(self,frame)
    captures.append((frame,time.monotonic()-started))
    return result

water_flow.FLIPWaterFlowProvider.sample = sample
pump_times = []
def tick():
    started = time.monotonic()
    water_preparation._pump()
    pump_times.append(time.monotonic()-started)
    time.sleep(.005)

def wait():
    deadline = time.monotonic()+60
    while water_preparation.active():
        assert time.monotonic() < deadline
        tick()
    assert bpy.context.scene.frame_current == original_frame
    assert bpy.context.scene.frame_subframe == original_subframe

try:
    water_preparation.start(bpy.context,(obj,),1,3,wait_for_window=False)
    wait()
    assert shared_controller.snapshot().state is BakeState.FINISHED
    assert output.is_file()
    first_count = len(captures)
    before = output.read_bytes()
    water_preparation.start(bpy.context,(obj,),1,3,wait_for_window=False)
    wait()
    assert len(captures) == first_count, 'Cache reuse reread particles'
    assert output.read_bytes() == before
    job = water_preparation.start(bpy.context,(obj,),1,5,wait_for_window=False)
    deadline = time.monotonic()+15
    while len(captures) == first_count:
        assert time.monotonic() < deadline
        tick()
    water_preparation.cancel(job)
    wait()
    assert shared_controller.snapshot().state is BakeState.CANCELLED
    assert output.read_bytes() == before
    assert not list(output.parent.glob(output.name+'.*.tmp'))
    print('WATER PREPARATION PASS: actual FLIP conversion, verified reuse, Cancel preserves old cache, timeline restored')
    print('MAX MAIN-THREAD PUMP SECONDS',max(pump_times))
    print('CAPTURE SECONDS',captures)
finally:
    water_preparation.shutdown()
    water_flow.FLIPWaterFlowProvider.sample = original_sample
