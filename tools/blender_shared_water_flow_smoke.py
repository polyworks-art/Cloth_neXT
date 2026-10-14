"""Real Blender RNA sharing, scene isolation and save/reload; no solver or user files."""
from pathlib import Path
import sys
import tempfile
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloth_next
from cloth_next.blender import water_settings

cloth_next.register()
scene=bpy.context.scene
scene_name=scene.name


def cloth(name, target=scene, role='CLOTH'):
    mesh=bpy.data.meshes.new(name)
    mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
    obj=bpy.data.objects.new(name,mesh)
    target.collection.objects.link(obj)
    obj.cloth_next.role=role
    obj.cloth_next.enabled=True
    return obj


a,b=cloth('SharedA'),cloth('SharedB')
collider=cloth('UntouchedCollider',role='COLLIDER')
other_scene=bpy.data.scenes.new('IndependentFlowScene')
other=cloth('OtherSceneCloth',target=other_scene)
domain=bpy.data.objects.new('FlowDomain',None)
scene.collection.objects.link(domain)
b.cloth_next.water_flow_influence=.75
b.cloth_next.water_flow_velocity_scale=1.5
a.cloth_next.water_flow_influence=.25
a.cloth_next.water_flow_velocity_scale=.5
a.cloth_next.water_flow_domain=domain
a.cloth_next.water_flow_container='//SharedFlow.gaia'
a.cloth_next.water_flow_resolution='CUSTOM'
a.cloth_next.water_flow_custom_resolution=128
a.cloth_next.water_flow_coupling_strength=3
a.cloth_next.water_flow_native_flow=.4
a.cloth_next.water_flow_enabled=True
assert all(getattr(a.cloth_next,key)==getattr(b.cloth_next,key)
           for key in water_settings.SHARED_PROPERTIES)
assert not collider.cloth_next.water_flow_enabled
assert not other.cloth_next.water_flow_enabled
assert b.cloth_next.water_flow_influence==.75
assert b.cloth_next.water_flow_velocity_scale==1.5
b.cloth_next.water_flow_enabled=False
assert not a.cloth_next.water_flow_enabled
b.cloth_next.enabled=False
c=cloth('SharedNewCloth')
assert not c.cloth_next.water_flow_enabled
assert c.cloth_next.water_flow_domain==domain
assert c.cloth_next.water_flow_container=='//SharedFlow.gaia'
c.cloth_next.water_flow_enabled=True
b.cloth_next.enabled=True
assert a.cloth_next.water_flow_enabled and b.cloth_next.water_flow_enabled
other.cloth_next.water_flow_container='//Independent.gaia'
other.cloth_next.water_flow_enabled=True
assert a.cloth_next.water_flow_container=='//SharedFlow.gaia'

with tempfile.TemporaryDirectory(prefix='cnx-shared-flow-') as temporary:
    path=Path(temporary)/'sharing.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    a,b=bpy.data.objects['SharedA'],bpy.data.objects['SharedB']
    scene=bpy.data.scenes[scene_name]
    assert a.cloth_next.water_flow_enabled and b.cloth_next.water_flow_enabled
    assert b.cloth_next.water_flow_influence==.75
    assert b.cloth_next.water_flow_velocity_scale==1.5
    assert a.cloth_next.water_flow_influence==.25
    assert a.cloth_next.water_flow_velocity_scale==.5
    b.cloth_next.water_flow_custom_resolution=200
    assert a.cloth_next.water_flow_custom_resolution==200
    assert bpy.data.objects['OtherSceneCloth'].cloth_next.water_flow_container=='//Independent.gaia'
    # Migration of an old file with one enabled object, bypassing callbacks.
    scene.cloth_next_water_flow_source=None
    scene.cloth_next_water_flow_shared=False
    a.cloth_next['water_flow_enabled']=True
    b.cloth_next['water_flow_enabled']=False
    water_settings._load_post()
    assert b.cloth_next.water_flow_enabled
    assert b.cloth_next.water_flow_influence==.75
    # Resolve one automatic .gaia destination despite different Cloth cache folders.
    from cloth_next.blender import water_preparation
    a.cloth_next.water_flow_container=''
    a.cloth_next.cache_directory=str(Path(temporary)/'CacheA')
    b.cloth_next.cache_directory=str(Path(temporary)/'CacheB')
    job=water_preparation.start(bpy.context,(a,b),1,2,wait_for_window=False)
    try:
        assert a.cloth_next.water_flow_container==b.cloth_next.water_flow_container
        assert Path(a.cloth_next.water_flow_container).suffix=='.gaia'
        assert Path(a.cloth_next.water_flow_container).parent==Path(temporary)/'CacheA'
    finally:
        water_preparation.cancel(job)
    # Deleting the canonical source retains the shared setup on its peers.
    source=scene.cloth_next_water_flow_source
    bpy.data.objects.remove(source,do_unlink=True)
    water_settings._depsgraph_post(scene,None)
    assert scene.cloth_next_water_flow_source is not None

cloth_next.unregister()
assert not hasattr(bpy.types.Scene,'cloth_next_water_flow_source')
assert not any(getattr(h,'_clothnext_shared_water',False) for h in bpy.app.handlers.load_post)
assert not any(getattr(h,'_clothnext_shared_water',False) for h in bpy.app.handlers.depsgraph_update_post)
print('SHARED WATER FLOW PASS: RNA changes, individual influence/scale, new Cloth, scene isolation, reload, migration, source deletion, cleanup')
