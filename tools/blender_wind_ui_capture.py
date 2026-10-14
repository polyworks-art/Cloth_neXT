"""Capture the real Wind controls in an isolated Blender window."""
import sys,traceback
from pathlib import Path
import bpy
bpy.context.preferences.view.show_splash=False
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.blender import registration
registration.register()
bpy.ops.object.empty_add()
obj=bpy.context.object; obj.cloth_next.enabled=True; obj.cloth_next.role='FORCE'
force=obj.cloth_next.force
force.wind_strength=5; force.wind_variation=1; force.wind_turbulence=.5
force.wind_show_vectors=True
area=next(area for area in bpy.context.screen.areas if area.type=='VIEW_3D')
area.type='PROPERTIES'; area.spaces.active.context='PHYSICS'
out=ROOT/'.tmp_verify/wind-controls.png'
def capture():
    try:
        region=next(region for region in area.regions if region.type=='WINDOW')
        with bpy.context.temp_override(area=area,region=region):
            bpy.ops.screen.screenshot_area(filepath=str(out))
    except Exception:
        (ROOT/'.tmp_verify/wind-controls-error.txt').write_text(traceback.format_exc())
    finally:
        bpy.ops.wm.quit_blender()
    return None
bpy.app.timers.register(capture,first_interval=3.)
