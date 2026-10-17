"""Open the isolated, baked NEON copy for actual GUI and playback inspection."""
import argparse
import sys
from pathlib import Path
import bpy
import addon_utils
from mathutils import Vector,Quaternion
import math
import json

p=argparse.ArgumentParser()
p.add_argument('--repo',type=Path,required=True)
p.add_argument('--blend',type=Path,required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(a.repo));addon_utils.enable('cloth_next',default_set=True)
for name in ('superhivemarket_com','api_portal_polyhammer_com'):
    if not any(r.module==name for r in bpy.context.preferences.extensions.repos):
        bpy.context.preferences.extensions.repos.new(name=name,module=name)
for name in ('bl_ext.superhivemarket_com.flip_fluids_addon',
             'bl_ext.api_portal_polyhammer_com.character_dna',
             'bl_ext.api_portal_polyhammer_com.character_assembly'):
    addon_utils.enable(name,default_set=True)
bpy.ops.wm.open_mainfile(filepath=str(a.blend))
from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller,engine
controller.register();controller.after_load()
for obj in bpy.context.scene.objects:
    if obj.animation_data:
        for curve in obj.animation_data.drivers:
            if engine.NAMESPACE in curve.driver.expression:curve.driver.expression=curve.driver.expression
    obj.update_tag()
objects=[o for o in bpy.context.scene.objects if o.cloth_next.enabled and o.cloth_next.role=='CLOTH']
bpy.ops.object.select_all(action='DESELECT')
for obj in objects:obj.select_set(True)
bpy.context.view_layer.objects.active=objects[0]
bpy.context.scene.frame_start=objects[0].cloth_next.bake_start
bpy.context.scene.frame_end=objects[0].cloth_next.bake_end
bpy.context.scene.frame_set(bpy.context.scene.frame_end)
for obj in bpy.context.scene.objects:
    if getattr(getattr(obj,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN':obj.hide_set(True)
def configure_ui():
    if bpy.context.screen is None:return .5
    prefs=bpy.context.preferences.addons['cloth_next'].preferences
    prefs.auto_launch_bake_window=False
    prefs.new_look=False
    from cloth_next.bake.controller import shared_controller
    def state(snapshot):
        (a.blend.parent/'gui-status.json').write_text(json.dumps(dict(state=snapshot.state.value,
            active=snapshot.active,error=snapshot.error_summary,message=snapshot.status_message),indent=2))
    shared_controller.subscribe(state)
    for area in bpy.context.screen.areas:
        if area.type=='PROPERTIES':area.spaces.active.context='PHYSICS'
        if area.type=='VIEW_3D':
            view=area.spaces.active
            view.region_3d.view_location=Vector((0,0,.8))
            view.region_3d.view_distance=2.5
            view.region_3d.view_rotation=Quaternion((1,0,0),math.pi/2)
            view.shading.type='SOLID'
            view.overlay.show_extras=False
            view.overlay.show_relationship_lines=False
    print('ISOLATED_NEON_GUI_READY',str(a.blend),flush=True)
    return None
bpy.app.timers.register(configure_ui,first_interval=1.)
