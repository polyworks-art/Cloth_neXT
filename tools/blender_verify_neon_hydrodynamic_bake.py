"""Verify a saved result from the real Bake operator without another solve."""
import argparse
import json
from pathlib import Path
import sys
import traceback
import importlib
import bpy
import addon_utils
import numpy as np

p=argparse.ArgumentParser()
p.add_argument('--repo',type=Path,required=True)
p.add_argument('--run',type=Path,required=True)
p.add_argument('--addon-module', default='cloth_next')
p.add_argument('--owner-manifest', type=Path)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
if a.addon_module == 'cloth_next':sys.path.insert(0,str(a.repo))
report={'result':'RUNNING','source_run':str(a.run),'blender':bpy.app.version_string}
try:
    addon_utils.enable(a.addon_module,default_set=True)
    if a.owner_manifest:
        bpy.context.preferences.addons[a.addon_module].preferences.gaia_owner_manifest=str(a.owner_manifest.resolve())
    for name in ('superhivemarket_com','api_portal_polyhammer_com'):
        if not any(r.module==name for r in bpy.context.preferences.extensions.repos):
            bpy.context.preferences.extensions.repos.new(name=name,module=name)
    for name in ('bl_ext.superhivemarket_com.flip_fluids_addon',
                 'bl_ext.api_portal_polyhammer_com.character_dna',
                 'bl_ext.api_portal_polyhammer_com.character_assembly'):
        addon_utils.enable(name,default_set=True)
    bpy.ops.wm.open_mainfile(filepath=str(a.run/'NEON-test.blend'))
    from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller,engine
    controller.register();controller.after_load()
    for obj in bpy.context.scene.objects:
        if obj.animation_data:
            for curve in obj.animation_data.drivers:
                if engine.NAMESPACE in curve.driver.expression:curve.driver.expression=curve.driver.expression
        obj.update_tag()
    def module(name):return importlib.import_module(a.addon_module + '.' + name)
    solver_test=module('blender.solver_test');export_identity=module('export_identity')
    io_path=module('core.filesystem_paths').io_path
    read_header=module('bake.pc2').read_header;iter_frames=module('bake.pc2').iter_frames
    parse_output_map=module('ppf.results').parse_output_map
    ZUP_TO_YUP=module('ppf.coordinates').ZUP_TO_YUP
    source=json.loads((a.run/'report.json').read_text())
    assert source['state']=='FINISHED',source.get('error')
    maps=list(io_path(a.run,reserved_length=1000).rglob('map.pickle'))
    assert len(maps)==1,[str(p) for p in maps]
    mapping=parse_output_map(maps[0].read_bytes(),schema_version=2)
    native=maps[0].parent/'output';report['native_output']=str(native)
    report['hydrodynamics']=[]
    for path in sorted(native.glob('hydro_*.bin')):
        values=np.fromfile(path,dtype='<f4').reshape(-1,16)
        assert np.isfinite(values).all()
        report['hydrodynamics'].append({'file':path.name,'area_m2':float(values[:,0].sum()),
            'wet_area_m2':float(values[:,1].sum()),'normal_load_n':float(values[:,2].sum()),
            'tangential_load_n':float(values[:,3].sum())})
    if not source.get('dry',False):assert report['hydrodynamics']
    report['playback']=[]
    objects=[o for o in bpy.context.scene.objects if o.cloth_next.enabled and o.cloth_next.role=='CLOTH']
    assert len(objects)==3
    # Evaluate the scene once per frame, then inspect every garment. The same
    # complete PC2/native/reload checks apply to every object and every sample.
    first,last=source['range']
    bpy.context.scene.frame_set(first)
    checks=[]
    for obj in objects:
        mods=[m for m in obj.modifiers if solver_test.is_cloth_next_playback_modifier(obj,m) and m.show_viewport]
        assert len(mods)==1
        path=Path(bpy.path.abspath(mods[0].filepath));header=read_header(path)
        assert header.frame_count==last-first+1
        world=np.asarray(ZUP_TO_YUP)@np.asarray(obj.matrix_world)
        indices=mapping.indices_for(export_identity.export_uuid(obj),header.vertex_count)
        record={'object':obj.name,'frames':header.frame_count,'pc2':str(path),
                'native_max_error_m':0.,'reload_playback_max_error_m':0.}
        report['playback'].append(record)
        checks.append((obj,world,list(indices),list(iter_frames(path)),record))
    for sample in range(last-first+1):
        raw=(np.fromfile(native/f'vert_{sample}.bin',dtype='<f4').reshape(-1,3)
             if sample else None)
        bpy.context.scene.frame_set(first+sample)
        graph=bpy.context.evaluated_depsgraph_get()
        for obj,world,indices,frames,record in checks:
            expected=frames[sample]
            if sample:
                cached_world=expected@world[:3,:3].T+world[:3,3]
                error=float(np.max(np.abs(cached_world-raw[indices])))
                record['native_max_error_m']=max(record['native_max_error_m'],error)
            evaluated=obj.evaluated_get(graph);mesh=evaluated.to_mesh()
            try:
                actual=np.asarray([tuple(v.co) for v in mesh.vertices])
                assert actual.shape==expected.shape,(obj.name,actual.shape,expected.shape)
                error=float(np.max(np.abs(actual-expected)))
                record['reload_playback_max_error_m']=max(record['reload_playback_max_error_m'],error)
            finally:evaluated.to_mesh_clear()
    for record in report['playback']:
        assert record['native_max_error_m']<1e-5,(record['object'],record['native_max_error_m'])
        assert record['reload_playback_max_error_m']<1e-5,(record['object'],record['reload_playback_max_error_m'])
    from dataclasses import asdict
    _bake_panel_model=module('blender.physics_ui')._bake_panel_model
    _solver_status=module('blender.physics_ui')._solver_status
    report['gui_model']=asdict(_bake_panel_model(bpy.context))
    report['gui_solver_status']=asdict(_solver_status(bpy.context))
    report['result']='PASS'
except BaseException:
    report['result']='FAIL';report['traceback']=traceback.format_exc();traceback.print_exc()
(a.run/'verification.json').write_text(json.dumps(report,indent=2))
print('NEON_PLAYBACK_VERIFICATION',json.dumps(report),flush=True)
bpy.ops.wm.quit_blender()
