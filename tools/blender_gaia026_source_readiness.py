"""Read-only FLIP cache readiness; no preparation, bake or saved mutations."""
import argparse,hashlib,json,sys,re
from pathlib import Path
import bpy,addon_utils
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--repo',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:]);sys.path.insert(0,str(a.repo))
before=hashlib.sha256(a.source.read_bytes()).hexdigest()
addon_utils.enable('cloth_next',default_set=True)
repos=bpy.context.preferences.extensions.repos
if not any(r.module=='superhivemarket_com' for r in repos):
    repos.new(name='FLIP validation',module='superhivemarket_com')
addon_utils.enable('bl_ext.superhivemarket_com.flip_fluids_addon',default_set=True)
bpy.ops.wm.open_mainfile(filepath=str(a.source))
domains=[o for o in bpy.context.scene.objects if getattr(getattr(o,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN']
report=dict(result='UNREADY',source=str(a.source),domains=[])
report['scene_meshes']=[dict(name=o.name,vertices=len(o.data.vertices),faces=len(o.data.polygons),
    enabled=o.cloth_next.enabled,role=o.cloth_next.role,pinning_enabled=o.cloth_next.pinning_enabled,
    pin_group=o.cloth_next.pin_group,pin_mode=o.cloth_next.pin_mode,
    pin_constraint_type=o.cloth_next.pin_constraint_type,
    vertex_groups=[g.name for g in o.vertex_groups],modifiers=[dict(name=m.name,type=m.type) for m in o.modifiers])
    for o in bpy.context.scene.objects if o.type=='MESH']
for domain in domains:
    cache=Path(domain.flip_fluid.domain.cache.get_cache_abspath())
    files=list((cache/'bakefiles').glob('*'))
    particle=[f for f in files if re.fullmatch(r'(?:fluid|liquid)particles\d+\.\w+',f.name)]
    velocity=[f for f in files if re.fullmatch(r'(?:fluid|liquid)particlesvelocity\d+\.\w+',f.name)]
    record=dict(name=domain.name,cache=str(cache),particle_files=len(particle),velocity_files=len(velocity),
        saved_scene_range=[bpy.context.scene.frame_start,bpy.context.scene.frame_end],
        fps=bpy.context.scene.render.fps/bpy.context.scene.render.fps_base,
        velocity_examples=[f.name for f in velocity[:3]],files_examples=[f.name for f in files[:10]])
    try:
        from cloth_next.blender.water_flow import FLIPWaterFlowProvider
        provider=FLIPWaterFlowProvider(bpy.context,domain,range(1,251),hash_sources=False)
        positions,velocities=provider.sample(1)
        record.update(provider_accepts_full_range=True,first_frame_particles=len(positions),provider=provider.identity)
        report['result']='READABLE_SNAPSHOT'
    except Exception as exc:record.update(provider_accepts_full_range=False,error=repr(exc))
    report['domains'].append(record)
report['source_blend_unchanged']=hashlib.sha256(a.source.read_bytes()).hexdigest()==before
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
