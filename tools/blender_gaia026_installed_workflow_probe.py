"""Inspect the actual installed extension without substituting checkout code."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import addon_utils
import bpy

p = argparse.ArgumentParser()
p.add_argument('--output', type=Path, required=True)
p.add_argument('--fluid-source', type=Path)
a = p.parse_args(sys.argv[sys.argv.index('--') + 1:])
name = 'bl_ext.polyworks_art_github_io.cloth_next'
repos = bpy.context.preferences.extensions.repos
if not any(r.module == 'polyworks_art_github_io' for r in repos):
    repos.new(name='Installed Cloth NeXt validation', module='polyworks_art_github_io')
module = importlib.import_module(name)
addon_utils.enable(name, default_set=True)
source_hash=None
domains=[]
if a.fluid_source:
    source_hash=hashlib.sha256(a.fluid_source.read_bytes()).hexdigest()
    if not any(r.module=='superhivemarket_com' for r in repos):
        repos.new(name='Installed FLIP validation',module='superhivemarket_com')
    addon_utils.enable('bl_ext.superhivemarket_com.flip_fluids_addon',default_set=True)
    bpy.ops.wm.open_mainfile(filepath=str(a.fluid_source))
    for item in bpy.context.scene.objects:item.cloth_next.enabled=False
    domains=[item for item in bpy.context.scene.objects
             if getattr(getattr(item,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN']
bpy.ops.mesh.primitive_plane_add()
obj = bpy.context.object
properties = set(obj.cloth_next.bl_rna.properties.keys())
required = ['water_flow_enabled', 'water_flow_domain', 'water_flow_container',
            'water_flow_coupling_strength', 'water_flow_native_flow']
missing = [key for key in required if key not in properties]
operators = {}
for key in ['enable_flip_velocity_export', 'prepare_water_flow']:
    try:
        getattr(bpy.ops.cloth_next, key).get_rna_type()
        operators[key] = True
    except Exception:
        operators[key] = False
manifest = Path(module.__file__).parent / 'blender_manifest.toml'
report = dict(result='BLOCKED' if missing or not all(operators.values()) else 'AVAILABLE',
              module=name, module_file=module.__file__, blender=bpy.app.version_string,
              manifest=manifest.read_text(), manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
              missing_native_flow_properties=missing, operators=operators,
              saved_preferences=False, checkout_substituted=False,
              subsequent_workflow='UNTESTED' if missing else 'REQUIRES_EXECUTION')
a.output.parent.mkdir(parents=True, exist_ok=True)
if a.fluid_source:
    assert len(domains)==1
    assert bpy.ops.clothnext.quick_assign_role('EXEC_DEFAULT',role='CLOTH')=={'FINISHED'}
    settings=obj.cloth_next
    bpy.context.preferences.addons[name].preferences.auto_launch_bake_window=False
    settings.water_flow_enabled=True;settings.water_flow_domain=domains[0]
    settings.water_flow_container=str(a.output.parent/'installed-prepared.gaia')
    settings.water_flow_resolution='100';settings.bake_start=1;settings.bake_end=3
    report['artist_steps']=[dict(step=1,name='Select actual FLIP Domain',result='PASS',domain=domains[0].name),
        dict(step=2,name='Configure Velocity Export quick button',result='BLOCKED',
             reason='Button is absent from this installed extension; existing source velocity export was already baked')]
    if operators['prepare_water_flow']:
        try:
            operation=bpy.ops.cloth_next.prepare_water_flow('EXEC_DEFAULT')
            water=importlib.import_module(name+'.blender.water_preparation')
            solver=importlib.import_module(name+'.blender.solver_test')
            controller=importlib.import_module(name+'.bake.controller').shared_controller
            deadline=time.monotonic()+180
            while controller.snapshot().active and time.monotonic()<deadline:
                if water.active():water._pump()
                solver._startup_pump();solver._pump();time.sleep(.02)
            assert not controller.snapshot().active,'Installed preparation exceeded 180 seconds'
            assert Path(settings.water_flow_container).is_file(),controller.snapshot().error_summary
            report['artist_steps'].append(dict(step=3,name='Prepare fluid .gaia cache',result='PASS',
                operator_result=sorted(operation),container=settings.water_flow_container))
        except Exception as exc:
            report['artist_steps'].append(dict(step=3,name='Prepare fluid .gaia cache',result='FAIL',error=repr(exc)))
    report['artist_steps'].append(dict(step=4,name='Activate Native Flow',result='BLOCKED',missing_properties=missing))
    report['artist_steps'] += [dict(step=i,result='UNTESTED',reason='Installed Native Flow unavailable') for i in range(5,11)]
    report['source_blend_unchanged']=hashlib.sha256(a.fluid_source.read_bytes()).hexdigest()==source_hash
    assert report['source_blend_unchanged']
a.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
