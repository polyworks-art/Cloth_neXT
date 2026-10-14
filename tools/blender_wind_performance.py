"""Repeatable Wind workload: redraw, timeline and export (no native solver)."""
import sys, time, math, json
from pathlib import Path
from types import SimpleNamespace
import bpy
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.blender import registration, wind_fields
from cloth_next.ppf.schema import envelope
registration.register()
try:
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    for index in range(24):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=48,ring_count=24,radius=.18,
            location=((index%4-1.5)*.6,(index//4-2.5)*.5,0))
    bpy.ops.object.empty_add()
    obj=bpy.context.object; obj.cloth_next.enabled=True; obj.cloth_next.role='FORCE'
    force=obj.cloth_next.force; force.wind_strength=3
    force.wind_radius=3; force.wind_obstacles='SCENE'; force.wind_show_vectors=True
    force.wind_resolution=13; force.wind_turbulence=.3
    obj.rotation_euler.y=math.pi/2
    bpy.context.scene.frame_set(1)
    start=time.perf_counter(); wind_fields.preview(bpy.context); cold=time.perf_counter()-start
    start=time.perf_counter()
    for _ in range(10): wind_fields.preview(bpy.context)
    redraw=(time.perf_counter()-start)/10
    start=time.perf_counter()
    for frame in range(2,12):
        bpy.context.scene.frame_set(frame); wind_fields.preview(bpy.context)
    timeline=(time.perf_counter()-start)/10
    payload=envelope.dumps_envelope(envelope.KIND_PARAM,{'scene':{'air-density':.1},
        'force_field':{'grids':[],'scripts':[]}},schema_version=2)
    start=time.perf_counter()
    wind_fields.apply_wind_payload(bpy.context,payload,SimpleNamespace(protocol_version='0.23'),1,10)
    export=time.perf_counter()-start
    print('WIND_PERFORMANCE '+json.dumps(dict(cold=cold,redraw=redraw,timeline=timeline,export=export)))
finally: registration.unregister()
