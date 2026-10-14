"""Read-only inspection of the saved production Water Flow scene."""
import sys,json
from pathlib import Path
import bpy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
for module in tuple(sys.modules.values()):
    if getattr(module,'__name__','').startswith('bl_ext.') and getattr(module,'__name__','').endswith('.cloth_next'):
        try: module.unregister()
        except Exception: pass
from cloth_next.blender import onboarding_manager
onboarding_manager.register=lambda:None
import cloth_next
cloth_next.register()
bpy.ops.wm.open_mainfile(filepath=r'F:\GAIA_Watertest.blend')
def props(value):
    result={}
    for prop in value.bl_rna.properties:
        key=prop.identifier
        if key=='rna_type':continue
        try:
            v=getattr(value,key)
            if isinstance(v,(str,int,float,bool)):result[key]=v
            elif prop.type=='POINTER':
                result[key]=props(v) if isinstance(v,bpy.types.PropertyGroup) else getattr(v,'name',str(type(v)))
        except Exception:pass
    return result
scene=bpy.context.scene
result={'frame':scene.frame_current,'fps':scene.render.fps/scene.render.fps_base,
        'scene':props(scene.cloth_next_solver),'quality':props(scene.cloth_next_quality),'objects':[]}
for obj in scene.objects:
    if obj.type!='MESH':continue
    item={'name':obj.name,'vertices':len(obj.data.vertices),'matrix':[list(r) for r in obj.matrix_world],
          'settings':props(obj.cloth_next),'modifiers':[]}
    for mod in obj.modifiers:item['modifiers'].append({'name':mod.name,'type':mod.type,'props':props(mod)})
    result['objects'].append(item)
(ROOT/'dist/water-response').mkdir(exist_ok=True)
(ROOT/'dist/water-response/scene-inspection.json').write_text(json.dumps(result,indent=2))
print('WATER_RESPONSE_INSPECTION_COMPLETE',flush=True)
import numpy as np
obj=scene.objects['Plane']
positions=[];normals=[]
for frame in range(1,251):
    scene.frame_set(frame)
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh=evaluated.to_mesh()
    p=np.empty(len(mesh.vertices)*3,dtype=np.float32);mesh.vertices.foreach_get('co',p)
    n=np.empty_like(p);mesh.vertices.foreach_get('normal',n)
    m=np.array(obj.matrix_world)
    positions.append(p.reshape(-1,3)@m[:3,:3].T+m[:3,3])
    normals.append(n.reshape(-1,3)@np.linalg.inv(m[:3,:3]))
    evaluated.to_mesh_clear()
pins=[]
group=obj.vertex_groups.get(obj.cloth_next.pin_group)
for v in obj.data.vertices:
    pins.append(any(g.group==group.index and g.weight>1e-6 for g in v.groups) if group else False)
np.savez_compressed(ROOT/'dist/water-response/real-cloth.npz',positions=positions,normals=normals,pins=pins)
print('WATER_RESPONSE_CLOTH_COMPLETE',flush=True)
