"""Separate Blender instance: capture real GPU overlays without saving the user's scene."""
import sys,math,traceback
from pathlib import Path
import bpy
import gpu
import numpy as np

from mathutils import Euler,Vector,Matrix
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Remove only Cloth NeXt in this isolated process, then register audited source.
for module in tuple(sys.modules.values()):
    if getattr(module,'__name__','').startswith('bl_ext.') and getattr(module,'__name__','').endswith('.cloth_next'):
        try:module.unregister()
        except Exception:pass
from cloth_next.blender import onboarding_manager
onboarding_manager.register=lambda:None
import cloth_next
cloth_next.register()
from cloth_next.blender.water_flow import FLIPWaterFlowProvider
scene=bpy.context.scene
scene.frame_set(150)
provider=FLIPWaterFlowProvider(bpy.context,scene.objects['FLIP Domain'],range(150,151),hash_sources=False)
provider.sample(150)
obj=scene.objects['Plane']
bpy.context.view_layer.objects.active=obj
s=obj.cloth_next
s.enabled=True;s.role='CLOTH';s.water_flow_enabled=True;s.water_flow_show_vectors=True
s.water_flow_container=str(ROOT/'dist/water-coordinate-audit.gaia')
s.water_flow_vector_stride=1;s.water_flow_vector_scale=.2;s.water_flow_show_bounds=True
window=bpy.context.window
area=next(a for a in window.screen.areas if a.type=='VIEW_3D')
region=next(r for r in area.regions if r.type=='WINDOW')
space=area.spaces.active
space.shading.type='SOLID'
space.overlay.show_overlays=True
space.overlay.show_floor=False;space.overlay.show_axis_x=False;space.overlay.show_axis_y=False
space.region_3d.view_location=(0,0,.7)
space.region_3d.view_distance=13
space.region_3d.view_rotation=Euler((math.radians(65),0,math.radians(35)),'XYZ').to_quaternion()
space.region_3d.view_perspective='PERSP'
scene.render.resolution_x=1000;scene.render.resolution_y=700;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG'
# The native FLIP surface stays visible; particle display is hidden only in this fixture.
particles=provider.cache.get_cache_object();particles.hide_set(True)
obj.select_set(False)
log=ROOT/'dist/water-overlay-capture-result.txt'
log.write_text('RUNNING')
state={'index':0}
modes=['POSITIONS','CONSTANT_X','DIRECTION','MAGNITUDE']
def capture():
    try:
        i=state['index']
        if i==len(modes):
            log.write_text('PASS: four native GPU overlays captured; explicit world matrices and occupied nodes')
            cloth_next.unregister();bpy.ops.wm.quit_blender();return None
        mode=modes[i];s.water_flow_debug_mode=mode
        area.tag_redraw()
        scene.render.filepath=str(ROOT/'dist'/('water-overlay-'+mode.lower()+'.png'))
        with bpy.context.temp_override(window=window,area=area,region=region,object=obj,active_object=obj):
            from cloth_next.blender import water_flow
            offscreen=gpu.types.GPUOffScreen(1000,700)
            eye=Vector((10,-13,11));target=Vector((0,0,.5))
            camera=Matrix.Translation(eye)@(target-eye).to_track_quat('-Z','Y').to_matrix().to_4x4()
            view=camera.inverted();f=1./math.tan(math.radians(30));near,far=.01,1000.
            projection=Matrix(((f/(1000/700),0,0,0),(0,f,0,0),
                (0,0,(far+near)/(near-far),2*far*near/(near-far)),(0,0,-1,0)))
            try:
                offscreen.draw_view3d(scene,bpy.context.view_layer,space,region,
                    view,projection)
                with offscreen.bind():
                    gpu.state.viewport_set(0,0,1000,700)
                    gpu.state.depth_test_set('NONE')
                    gpu.state.scissor_test_set(False)
                    water_flow._draw_vectors(_region=SimpleNamespace(view_matrix=view,window_matrix=projection))
                    (ROOT/'dist/water-overlay-debug.txt').write_text(str((bpy.context.object.name if bpy.context.object else None,s.enabled,s.role,s.water_flow_show_vectors,s.water_flow_container,bpy.context.region_data is not None,[(v[0],len(v[1])) for v in water_flow._preview.values()])))
                    pixels=gpu.state.active_framebuffer_get().read_color(0,0,1000,700,4,0,'UBYTE')
                    data=np.asarray(pixels).reshape(700,1000,4)
                    cyan=(data[:,:,0]<80)&(data[:,:,1]>100)&(data[:,:,2]>200)
                    print('CYAN PIXELS',mode,int(np.count_nonzero(cyan)),flush=True)
                    assert np.count_nonzero(cyan) > 50, f'{mode}: no visible overlay pixels'
                    # Compare GPU raster extents with projected CPU endpoints.
                    vertices=next(iter(water_flow._preview.values()))[1]
                    assert vertices.dtype == np.float32 and vertices.flags.c_contiguous
                    homogeneous=np.column_stack((vertices,np.ones(len(vertices))))
                    clip=homogeneous@np.asarray(projection@view).T
                    assert (clip[:,3]>0).all()
                    screen=(clip[:,:2]/clip[:,3,None]+1)*np.asarray((500.,350.))
                    yy,xx=np.nonzero(cyan)
                    actual=np.column_stack((xx,yy))
                    assert (actual>=screen.min(0)-5).all() and (actual<=screen.max(0)+5).all(), f'{mode}: GPU pixels extend beyond projected endpoints'
                    image=bpy.data.images.new('Water Audit Capture',width=1000,height=700,alpha=True)
                    image.pixels.foreach_set((data.astype(np.float32)/255.).reshape(-1))
                    image.filepath_raw=scene.render.filepath;image.file_format='PNG';image.save()
                    bpy.data.images.remove(image)
            finally:
                offscreen.free()
        state['index']+=1
        return .5
    except Exception:
        log.write_text(traceback.format_exc());bpy.ops.wm.quit_blender();return None
bpy.app.timers.register(capture,first_interval=2.)
