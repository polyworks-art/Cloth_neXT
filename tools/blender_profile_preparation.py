"""Read-only scene profile of preparation frame evaluation and FLIP display overhead."""
import argparse
import cProfile
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import addon_utils
import bpy
import numpy as np

p=argparse.ArgumentParser()
p.add_argument('--blend',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--frames',type=int,default=3)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
source_sha=hashlib.sha256(a.blend.read_bytes()).hexdigest()
for module in ('superhivemarket_com','api_portal_polyhammer_com'):
    if not any(repo.module==module for repo in bpy.context.preferences.extensions.repos):
        bpy.context.preferences.extensions.repos.new(name='Profile dependencies',module=module)
for name in ('bl_ext.superhivemarket_com.flip_fluids_addon',
             'bl_ext.api_portal_polyhammer_com.character_dna',
             'bl_ext.api_portal_polyhammer_com.character_assembly'):
    addon_utils.enable(name,default_set=True)
import cloth_next
cloth_next.register()
bpy.ops.wm.open_mainfile(filepath=str(a.blend))
from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller,engine
controller.register();controller.after_load()
for obj in bpy.context.scene.objects:
    if obj.animation_data:
        for curve in obj.animation_data.drivers:
            if engine.NAMESPACE in curve.driver.expression:
                curve.driver.expression=curve.driver.expression
                obj.update_tag()
scene=bpy.context.scene
# Isolate CPU preparation in headless Blender; GPU drawing itself needs a
# window. Keep debug-file loading enabled on the baseline path.
draw=importlib.import_module('bl_ext.superhivemarket_com.flip_fluids_addon.operators.draw_particles_operators')
draw.update_debug_particle_geometry=lambda _context:None
scene.frame_set(2);scene.frame_set(1)
domains=[obj for obj in scene.objects
         if getattr(getattr(obj,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN']
modules={importlib.import_module(type(obj.flip_fluid.domain.mesh_cache.surface).__module__)
         for obj in domains}
actors=[obj for obj in scene.objects if obj.cloth_next.enabled
        and obj.cloth_next.role in {'CLOTH','COLLIDER'} and obj.name!='SW_04_body_lod0_mesh.001']
from cloth_next.blender import solver_test
from cloth_next.blender.preparation_preview import frame_set as preparation_frame_set


def capture(quiet):
    previous={module:module.DISABLE_MESH_CACHE_LOAD for module in modules}
    profiler=cProfile.Profile();samples=[];started=time.perf_counter()
    try:
        profiler.enable()
        for frame in range(1,a.frames+1):
            (preparation_frame_set if quiet else lambda target,number:target.frame_set(number))(scene,frame)
            values=[]
            for obj in actors:
                with solver_test._evaluate_through_solver_input_modifiers(bpy.context,obj) as boundary:
                    evaluated=(boundary or obj).evaluated_get(bpy.context.evaluated_depsgraph_get())
                    mesh=evaluated.to_mesh()
                    try:
                        coords=np.empty(len(mesh.vertices)*3,dtype=np.float32)
                        mesh.vertices.foreach_get('co',coords)
                        values.append((obj.name,coords.copy(),np.asarray(obj.matrix_world).copy()))
                    finally:evaluated.to_mesh_clear()
            samples.append(values)
        profiler.disable()
    finally:
        for module,value in previous.items():module.DISABLE_MESH_CACHE_LOAD=value
    elapsed=time.perf_counter()-started
    profiler.create_stats()
    top=sorted(profiler.stats.items(),key=lambda pair:pair[1][3],reverse=True)[:15]
    return samples,elapsed,[dict(function=key[2],file=Path(key[0]).name,
        calls=value[1],self_seconds=value[2],total_seconds=value[3]) for key,value in top]


baseline,baseline_seconds,baseline_profile=capture(False)
quiet,quiet_seconds,quiet_profile=capture(True)
exact=all(name==other_name and np.array_equal(vertices,other_vertices)
          and np.array_equal(matrix,other_matrix)
          for left,right in zip(baseline,quiet)
          for (name,vertices,matrix),(other_name,other_vertices,other_matrix) in zip(left,right))
result=dict(result='PASS' if exact else 'FAIL',frames=a.frames,actors=[obj.name for obj in actors],
    baseline_seconds=baseline_seconds,quiet_flip_seconds=quiet_seconds,
    physics_input_vertices_and_matrices_exact=exact,
    source_unchanged=source_sha==hashlib.sha256(a.blend.read_bytes()).hexdigest(),
    optimization='Only FLIP GPU debug previews masked during frame_set; physical cache meshes retained',
    headless_gpu_drawing_stub=True,
    baseline_profile=baseline_profile,quiet_profile=quiet_profile)
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(result,indent=2))
print(json.dumps({key:value for key,value in result.items() if not key.endswith('_profile')}))
assert exact and result['source_unchanged']
