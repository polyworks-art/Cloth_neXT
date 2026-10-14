from pathlib import Path
import sys,uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bpy
import cloth_next
from cloth_next.blender import solver_test as bake
from cloth_next.bake.frame_range import BakeFrameRange
cloth_next.register()
obj=bpy.context.object
obj.cloth_next.enabled=True
obj.cloth_next.role='COLLIDER'
obj.cloth_next.collider_motion='ANIMATED'
obj.cloth_next.collider_capture_mode='DEFORMING'
obj.cloth_next.collider_samples_per_frame=1
bake.export_identity.ensure_unique_persistent_ids((obj,))
driver=obj.driver_add('location',0).driver
driver.expression='frame * 0.1'
cache=bake.ExportPayloadCache(Path(__file__).resolve().parents[1]/'.tmp_verify'/('driver-collider-'+uuid.uuid4().hex))
r=BakeFrameRange(1,3)
key,reason=bake._animated_collider_cache_key(bpy.context,obj,r)
assert key is None, (key,reason)
def capture():
 return bake._capture_animated_colliders_shared(bpy.context,(obj,),r)[obj.name]
a=capture()
a,hit=bake._verified_collider_capture(cache,a)
assert not hit
a.cleanup()
b=capture()
b,hit=bake._verified_collider_capture(cache,b)
assert hit
b.cleanup()
driver.expression='frame * 0.1 + (0.2 if frame == 3 else 0.0)'
bpy.context.view_layer.update()
c=capture()
c,hit=bake._verified_collider_capture(cache,c)
assert not hit
c.cleanup()
print('DRIVER_COLLIDER_CACHE_SMOKE_OK: identical evaluated animation reused; late-frame driver change invalidated')
cloth_next.unregister()

