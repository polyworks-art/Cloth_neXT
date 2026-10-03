# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare the real source through the existing asynchronous bake pipeline."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import time
import bpy

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from cloth_next.blender import water_preparation
from cloth_next.bake.controller import shared_controller
from cloth_next.bake.status import BakeState

output=ROOT/'dist/gaia-streaming/real-100-250.gaia'
output.parent.mkdir(parents=True,exist_ok=True)
obj=SimpleNamespace(name='Water Streaming Audit',cloth_next=SimpleNamespace(
    water_flow_enabled=True,enabled=True,role='CLOTH',
    water_flow_domain=bpy.context.scene.objects['FLIP Domain'],water_flow_influence=1.,
    water_flow_velocity_scale=1.,water_flow_resolution='AUTO',water_flow_custom_resolution=100,
    water_flow_container=str(output),cache_directory=''))
started=time.perf_counter();original=bpy.context.scene.frame_current
try:
    water_preparation.start(bpy.context,(obj,),1,250,wait_for_window=False)
    last=0
    while water_preparation.active():
        if time.perf_counter()-started>1800:raise TimeoutError('Real Water Flow preparation timed out')
        water_preparation._pump()
        current=bpy.context.scene.frame_current
        if current>=last+25:
            last=current;print('WATER PREPARE',current,flush=True)
        time.sleep(.005)
    assert shared_controller.snapshot().state is BakeState.FINISHED,shared_controller.snapshot()
    assert bpy.context.scene.frame_current==original
    print(json.dumps({'path':str(output),'seconds':time.perf_counter()-started,
                      'bytes':output.stat().st_size}),flush=True)
finally:water_preparation.shutdown()
