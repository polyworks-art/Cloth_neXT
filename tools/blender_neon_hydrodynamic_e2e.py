"""Actual NEON Bake operator in an isolated profile; preserve the entire scene."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import shutil
import time
import traceback
import importlib

import addon_utils
import bpy
import numpy as np


class ExpectedCancellation(Exception):
    pass


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--blend',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--first',type=int,default=130)
    parser.add_argument('--last',type=int,default=133)
    parser.add_argument('--dry',action='store_true')
    parser.add_argument('--addon-module', default='cloth_next')
    parser.add_argument('--owner-manifest', type=Path)
    parser.add_argument('--flip-cache', type=Path)
    parser.add_argument('--backend', choices=('CUDA', 'CPU'), default='CUDA')
    parser.add_argument('--cancel-at-stage', choices=('PREPARING_WATER', 'EXPORTING', 'SIMULATING'))
    parser.add_argument('--modify-material', action='store_true', help='Negative test: change the frozen material')
    parser.add_argument('--expect-error', help='Negative test: require this error text and no successful bake')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    for name in ('repo','blend','output'):
        setattr(args,name,getattr(args,name).resolve())
    args.output.mkdir(parents=True,exist_ok=False)
    report={'result':'RUNNING','blender':bpy.app.version_string,'range':[args.first,args.last],
            'entry_point':'bpy.ops.clothnext.bake(EXEC_DEFAULT)','states':[],'dry':args.dry}
    report_path=args.output/'report.json'
    def save():report_path.write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    if args.addon_module == 'cloth_next':
        sys.path.insert(0,str(args.repo))
    try:
        addon_utils.enable(args.addon_module,default_set=True)
        repos=bpy.context.preferences.extensions.repos
        for module in ('superhivemarket_com','api_portal_polyhammer_com'):
            if not any(r.module==module for r in repos):repos.new(name=module,module=module)
        for module in ('bl_ext.superhivemarket_com.flip_fluids_addon',
                       'bl_ext.api_portal_polyhammer_com.character_dna',
                       'bl_ext.api_portal_polyhammer_com.character_assembly'):
            addon_utils.enable(module,default_set=True)
        bpy.ops.wm.open_mainfile(filepath=str(args.blend))
        from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller,engine
        controller.register();controller.after_load()
        for obj in bpy.context.scene.objects:
            if obj.animation_data:
                for curve in obj.animation_data.drivers:
                    if engine.NAMESPACE in curve.driver.expression:curve.driver.expression=curve.driver.expression
            obj.update_tag()
        def module(name):return importlib.import_module(args.addon_module + '.' + name)
        solver_test=module('blender.solver_test');water_preparation=module('blender.water_preparation')
        require_hydrodynamic_capability=module('ppf.hydrodynamic_capability').require_hydrodynamic_capability
        read_header=module('bake.pc2').read_header;iter_frames=module('bake.pc2').iter_frames
        manifest_version=importlib.import_module(args.addon_module).manifest_version
        prefs=bpy.context.preferences.addons[args.addon_module].preferences
        if args.owner_manifest:prefs.gaia_owner_manifest=str(args.owner_manifest.resolve())
        prefs.auto_launch_bake_window=False
        prefs.solver_backend_choice=args.backend
        if args.dry:
            for obj in bpy.context.scene.objects:
                if hasattr(obj, 'cloth_next'):
                    obj.cloth_next.water_flow_enabled=False
        resolved=solver_test.resolve_solver(bpy.context)
        if not args.dry:require_hydrodynamic_capability(resolved.executable_path)
        report['solver']=str(resolved.executable_path)
        report['addon_version']=str(manifest_version())
        objects=[o for o in bpy.context.scene.objects if o.cloth_next.enabled and o.cloth_next.role=='CLOTH']
        assert len(objects)==3,[(o.name,o.cloth_next.role) for o in objects]
        domains=[o for o in bpy.context.scene.objects if getattr(getattr(o,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN']
        assert len(domains)==1
        # The source .blend uses a relative FLIP path. Keep that read-only
        # source explicit after copying/saving the test scene elsewhere.
        domains[0].flip_fluid.domain.cache.cache_directory=str(args.flip_cache) if args.flip_cache else 'F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache'
        report['objects']=[{'name':o.name,'vertices':len(o.data.vertices),
                           'material':o.cloth_next.material.preset} for o in objects]
        report['attachment_groups']=len(bpy.context.scene.cloth_next_object_attachments)
        report['attachment_count']=sum(len(item.points) for item in bpy.context.scene.cloth_next_object_attachments)
        assert report['attachment_count']==186,report['attachment_count']
        for obj in objects:
            s=obj.cloth_next
            s.bake_start=args.first;s.bake_end=args.last
            s.save_on_finish=True
            s.cache_directory=str(args.output/'cache')
            s.water_flow_enabled=not args.dry;s.water_hydrodynamic_pressure=not args.dry
            s.water_flow_domain=domains[0]
            s.water_flow_container=str(args.output/'Fluid.gaia')
            s.water_flow_influence=1.;s.water_flow_velocity_scale=1.
        if args.modify_material:
            objects[0].cloth_next.material.stretch_resistance += 1.
        bpy.context.view_layer.objects.active=objects[0]
        bpy.context.scene.cloth_next_recovery.enabled=True
        bpy.context.scene.cloth_next_recovery.save_on_finish=True
        bpy.context.scene.frame_set(args.first)
        initial_matrices={o.name:np.asarray(o.matrix_world).copy() for o in objects}
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'NEON-test.blend'))
        started=time.monotonic()
        def state(snapshot):
            records=report['states']
            if not records or records[-1]['state']!=snapshot.state.value:
                records.append({'state':snapshot.state.value,'seconds':time.monotonic()-started})
                save()
        unsubscribe=solver_test.shared_controller.subscribe(state)
        report['operator_result']=sorted(bpy.ops.clothnext.bake('EXEC_DEFAULT'))
        save()
        cancel_requested=False
        while solver_test.shared_controller.snapshot().active:
            if (args.cancel_at_stage and not cancel_requested and
                    solver_test.shared_controller.snapshot().state.value == args.cancel_at_stage):
                solver_test.request_cancel();cancel_requested=True
            if time.monotonic()-started>7200:
                solver_test.request_cancel();raise TimeoutError('Bake exceeded two hours')
            if solver_test._pin_capture is not None:solver_test._pin_capture_pump()
            if water_preparation.active():water_preparation._pump()
            solver_test._startup_pump();solver_test._pump()
            time.sleep(.02)
        unsubscribe()
        snapshot=solver_test.shared_controller.snapshot()
        report.update(state=snapshot.state.value,error=snapshot.error_summary,error_details=snapshot.error_details,
                      work_directory=str(solver_test._last_work_directory),seconds=time.monotonic()-started)
        if args.cancel_at_stage:
            assert cancel_requested and snapshot.state.value == 'CANCELLED', report
            report['cancel_at_stage']=args.cancel_at_stage
            report['published_pc2_after_cancel']=[str(path) for path in (args.output/'cache').glob('*.pc2')]
            if args.cancel_at_stage in ('PREPARING_WATER', 'EXPORTING'):
                assert not report['published_pc2_after_cancel'], report
            raise ExpectedCancellation()
        assert snapshot.state.value=='FINISHED',report
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'NEON-test.blend'))
        report['playback']=[]
        export_identity=module('export_identity')
        parse_output_map=module('ppf.results').parse_output_map
        ZUP_TO_YUP=module('ppf.coordinates').ZUP_TO_YUP
        io_path=module('core.filesystem_paths').io_path
        maps=list(io_path(Path(solver_test._last_work_directory),reserved_length=1000).rglob('map.pickle'))
        if not maps:maps=list(io_path(args.output,reserved_length=1000).rglob('map.pickle'))
        assert len(maps)==1,[str(p) for p in maps]
        native_map=parse_output_map(maps[0].read_bytes(),schema_version=2)
        native_output=maps[0].parent/'output'
        report['native_output']=str(native_output)
        telemetry=[]
        for path in sorted(native_output.glob('hydro_*.bin')):
            values=np.fromfile(path,dtype='<f4').reshape(-1,16)
            assert np.isfinite(values).all()
            telemetry.append({'file':path.name,'area_m2':float(values[:,0].sum()),
                'wet_area_m2':float(values[:,1].sum()),'normal_load_n':float(values[:,2].sum()),
                'tangential_load_n':float(values[:,3].sum())})
        if not args.dry:assert telemetry,'No native hydrodynamic telemetry'
        report['hydrodynamics']=telemetry
        for obj in objects:
            modifiers=[m for m in obj.modifiers if solver_test.is_cloth_next_playback_modifier(obj,m)
                       and m.filepath and m.show_viewport]
            assert len(modifiers)==1
            path=Path(bpy.path.abspath(modifiers[0].filepath));header=read_header(path)
            assert header.frame_count==args.last-args.first+1 and header.vertex_count==len(obj.data.vertices)
            error=0.
            native_error=0.
            indices=native_map.indices_for(export_identity.export_uuid(obj),len(obj.data.vertices))
            to_local=np.linalg.inv(np.asarray(ZUP_TO_YUP)@initial_matrices[obj.name])
            to_world=np.asarray(ZUP_TO_YUP)@initial_matrices[obj.name]
            for sample,expected in enumerate(iter_frames(path)):
                frame=args.first+sample
                if sample:
                    positions=np.fromfile(native_output/f'vert_{sample}.bin',dtype='<f4').reshape(-1,3)[list(indices)]
                    local=positions@to_local[:3,:3].T+to_local[:3,3]
                    cached_world=expected@to_world[:3,:3].T+to_world[:3,3]
                    native_error=max(native_error,float(np.max(np.abs(cached_world-positions))))
                bpy.context.scene.frame_set(frame)
                evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
                mesh=evaluated.to_mesh()
                try:
                    actual=np.asarray([tuple(v.co) for v in mesh.vertices])
                    assert actual.shape==expected.shape
                    error=max(error,float(np.max(np.abs(actual-expected))))
                finally:evaluated.to_mesh_clear()
            assert error<1e-5,error
            assert native_error<1e-5,native_error
            report['playback'].append({'object':obj.name,'pc2':str(path),'frames':header.frame_count,
                'max_error_m':error,'native_max_error_m':native_error,
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'NEON-test.blend'))
        bpy.ops.wm.open_mainfile(filepath=str(args.output/'NEON-test.blend'))
        for record in report['playback']:
            obj=bpy.context.scene.objects[record['object']]
            assert obj.cloth_next.water_hydrodynamic_pressure == (not args.dry)
            expected=list(iter_frames(Path(record['pc2'])))[-1]
            bpy.context.scene.frame_set(args.last)
            evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh=evaluated.to_mesh()
            try:reload_error=float(np.max(np.abs(np.asarray([tuple(v.co) for v in mesh.vertices])-expected)))
            finally:evaluated.to_mesh_clear()
            assert reload_error<1e-5,reload_error
            record['reload_max_error_m']=reload_error
        report['result']='PASS'
    except ExpectedCancellation:
        report['result']='PASS'
    except BaseException:
        report['result']='FAIL';report['traceback']=traceback.format_exc()
        if args.expect_error and args.expect_error in (report.get('error','')+'\n'+report['traceback']):
            report['result']='PASS'
            report['expected_failure_observed']=args.expect_error
        traceback.print_exc()
    finally:
        work=report.get('work_directory')
        if work and Path(work).is_dir():
            try:
                shutil.copytree(work,args.output/'session-evidence',symlinks=True)
                report['session_evidence']=str(args.output/'session-evidence')
            except OSError as exc:report['evidence_copy_error']=str(exc)
        save()
    print('NEON_HYDRO_E2E',json.dumps(report,default=str),flush=True)
    bpy.ops.wm.quit_blender()


if __name__=='__main__':main()
