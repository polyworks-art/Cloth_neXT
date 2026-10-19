import sys, math
from pathlib import Path
import bpy
from mathutils import Quaternion
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import sewing
cloth_next.register()
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)

def panel(name, x, z, n, curved=False):
    verts = [(x + side, i/n*3-1.5, z + (.22*math.sin(i/n*math.pi) if curved else 0)) for side in (0, 1) for i in range(n+1)]
    faces = [(i,i+1,n+2+i,n+1+i) for i in range(n)]
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj=bpy.data.objects.new(name,mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active=obj
    obj.select_set(True)
    bpy.ops.clothnext.add_physics()
    obj.select_set(False)
    return obj

first=panel('Bodice left',-1.8,0,24,True)
second=panel('Bodice right',.8,0,7,True)
third=panel('Same object boundaries',-1.8,-2,18)
sewing._commit(bpy.context.scene,first,second,tuple(range(25,50)),tuple(range(8)))
sewing._commit(bpy.context.scene,third,third,tuple(range(19)),tuple(range(19,38)))
sewing._commit(bpy.context.scene,first,second,tuple(range(25)),tuple(range(8,16)),True)
bpy.context.view_layer.objects.active=first
first.select_set(True)
for area in bpy.context.screen.areas:
    if area.type=='VIEW_3D':
        space=area.spaces.active
        space.overlay.show_floor=False
        space.overlay.show_axis_x=False
        space.overlay.show_axis_y=False
        space.region_3d.view_rotation=Quaternion((1,0,0,0))
        space.region_3d.view_location=(0,0,-.6)
        space.region_3d.view_distance=9
        space.region_3d.view_perspective='ORTHO'
        space.shading.light='STUDIO'
        space.shading.color_type='SINGLE'
        space.shading.single_color=(.21,.25,.31)
        area.tag_redraw()

third.hide_set(True)
bpy.context.scene.cloth_next_sewing_definitions.clear()
def begin():
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(area=area, region=region):
                bpy.ops.clothnext.edit_sewing('INVOKE_DEFAULT',mode='SEGMENT')
            break
    return None
bpy.app.timers.register(begin, first_interval=3)
output=Path(__file__).resolve().parents[1]/'docs/validation/sewing'
def observe():
    op = next(iter(sewing._editor_sessions.values()), None)
    state = dict(stage=op.stage if op else 'EXITED', seams=len(bpy.context.scene.cloth_next_sewing_definitions), feedback=op.feedback if op else '')
    import json
    (output/'gui-state.json').write_text(json.dumps(state),encoding='utf-8')
    bpy.ops.screen.screenshot(filepath=str(output/'gui-latest.png'))
    return 3
bpy.app.timers.register(observe,first_interval=5)
