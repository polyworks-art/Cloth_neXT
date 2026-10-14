# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the existing Bake operator with an explicitly requested candidate.

Run in factory-startup Blender. No preferences, source .blend or registry are
saved. A fresh output directory is required. Headless execution pumps the same
main-thread callbacks that Blender normally invokes through its timer service.
"""
import argparse
import json
import hashlib
from pathlib import Path
import sys
import time
import numpy as np
import zipfile

import addon_utils
import bpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lifecycle', action='store_true')
    parser.add_argument('--fluid-source', type=Path)
    parser.add_argument('--first', type=int, default=1)
    parser.add_argument('--last', type=int, default=3)
    parser.add_argument('--gaia', type=Path)
    parser.add_argument('--dry', action='store_true')
    parser.add_argument('--timeout', type=float, default=180.)
    parser.add_argument('--verify-reuse', action='store_true')
    parser.add_argument('--verify-corruption', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.repo.resolve()))
    addon_utils.enable('cloth_next', default_set=True)
    from cloth_next.blender import solver_test
    from cloth_next.ppf.resolver import SolverResolver, SolverResolutionContext
    from cloth_next.bake.pc2 import read_header, iter_frames
    resolved = SolverResolver(solver_test._version_probe).resolve(
        SolverResolutionContext(development_executable=args.solver.resolve()))
    if resolved is None:
        raise RuntimeError('The requested real solver is unavailable')
    # Select the candidate only inside this factory-startup test process.
    solver_test.resolve_solver = lambda _context: resolved
    bpy.context.preferences.addons['cloth_next'].preferences.auto_launch_bake_window = False
    source_hash = None
    source_stamps = {}
    center = (0,0,1)
    domain = None
    if args.fluid_source:
        source_hash = hashlib.sha256(args.fluid_source.read_bytes()).hexdigest()
        repos=bpy.context.preferences.extensions.repos
        if not any(r.module=='superhivemarket_com' for r in repos):
            repos.new(name='FLIP validation',module='superhivemarket_com')
        addon_utils.enable('bl_ext.superhivemarket_com.flip_fluids_addon',default_set=True)
        bpy.ops.wm.open_mainfile(filepath=str(args.fluid_source.resolve()))
        for obj in bpy.context.scene.objects:
            obj.cloth_next.enabled = False
        domains=[obj for obj in bpy.context.scene.objects
                 if getattr(getattr(obj,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN']
        assert len(domains)==1
        domain=domains[0]
        from cloth_next.blender.water_flow import FLIPWaterFlowProvider
        provider=FLIPWaterFlowProvider(bpy.context,domain,range(args.first,args.last+1))
        from cloth_next.gaia.preparation import source_stamp
        source_stamps={path:source_stamp(path) for path in provider.source_paths}
        positions,velocities=provider.sample(args.first)
        assert len(positions)>0
        center=positions[int(np.argmax(np.linalg.norm(velocities,axis=1)))].tolist()
        del positions,velocities,provider
        bpy.context.scene.use_gravity = False
    else:
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_plane_add(size=.02 if domain else 1, location=center)
    cloth = bpy.context.object
    cloth.name = 'Regular GAIA Bake Probe'
    assert bpy.ops.clothnext.quick_assign_role('EXEC_DEFAULT', role='CLOTH') == {'FINISHED'}
    cloth.cloth_next.bake_start = args.first
    cloth.cloth_next.bake_end = args.last
    cloth.cloth_next.cache_directory = str(args.output)
    bpy.context.scene.render.fps = 24
    if domain:
        cloth.cloth_next.water_flow_enabled = not args.dry
        cloth.cloth_next.water_flow_domain = domain
        cloth.cloth_next.water_flow_container = str(args.gaia or (args.output/'Fluid.gaia'))
        cloth.cloth_next.water_flow_resolution = '100'
        cloth.cloth_next.water_flow_coupling_strength = 2.
        cloth.cloth_next.water_flow_native_flow = 1.
    frame_count = args.last-args.first+1
    report = {'result': 'RUNNING', 'solver': str(resolved.executable_path),
              'blender': bpy.app.version_string, 'frames': frame_count, 'vertices': 4,
              'source_range':[args.first,args.last], 'dry':args.dry, 'center':center,
              'entry_point': 'bpy.ops.clothnext.bake(EXEC_DEFAULT)',
              'headless_timer_pump': True}
    started = time.monotonic()
    report['state_transitions'] = []
    def record_state(snapshot):
        states = report['state_transitions']
        if not states or states[-1]['state'] != snapshot.state.value:
            states.append({'state':snapshot.state.value,'seconds':time.monotonic()-started})
    unsubscribe_trace = solver_test.shared_controller.subscribe(record_state)
    def wait_for_bake():
        deadline = time.monotonic() + args.timeout
        while solver_test.shared_controller.snapshot().active:
            if time.monotonic() >= deadline:
                solver_test.request_cancel()
                raise TimeoutError(f'Regular Bake did not finish within {args.timeout} seconds')
            if solver_test._pin_capture is not None:
                solver_test._pin_capture_pump()
            from cloth_next.blender import water_preparation
            if water_preparation.active():
                water_preparation._pump()
            solver_test._startup_pump()
            solver_test._pump()
            time.sleep(.02)
        snapshot = solver_test.shared_controller.snapshot()
        if snapshot.state.value != 'FINISHED':
            report.update(state=snapshot.state.value, error=snapshot.error_summary,
                          error_details=snapshot.error_details)
            raise AssertionError(f'Regular Bake ended in {snapshot.state.value}: {snapshot.error_summary}')
        return snapshot

    def active_cache(obj):
        modifiers = [m for m in obj.modifiers
                     if solver_test.is_cloth_next_playback_modifier(obj, m)
                     and m.filepath and m.show_viewport]
        assert len(modifiers) == 1, len(modifiers)
        cache = Path(bpy.path.abspath(modifiers[0].filepath))
        header = read_header(cache)
        assert header.frame_count == frame_count and header.vertex_count == 4
        return modifiers[0], cache

    try:
        report['operator_result'] = sorted(bpy.ops.clothnext.bake('EXEC_DEFAULT'))
        snapshot = wait_for_bake()
        report.update(state=snapshot.state.value, error=snapshot.error_summary,
                      error_details=snapshot.error_details)
        boundary, cache = active_cache(cloth)
        samples = list(iter_frames(cache))
        max_error = 0.0
        for frame, expected in enumerate(samples, args.first):
            bpy.context.scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = cloth.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                for vertex, target in zip(mesh.vertices, expected):
                    max_error = max(max_error, *(abs(vertex.co[i] - target[i]) for i in range(3)))
            finally:
                evaluated.to_mesh_clear()
        assert max_error < 1e-5, max_error
        report.update(result='PASS', pc2=str(cache), playback_max_error=max_error)
        report['final_local_positions'] = samples[-1].tolist()
        work = solver_test._last_work_directory
        report['work_directory'] = str(work)
        if work:
            report['water_stream_status'] = [json.loads(p.read_text(encoding='utf-8'))
                                            for p in Path(work).glob('water-stream-*/status.json')]
            report['base_param_bytes'] = [p.stat().st_size
                                         for p in Path(work).glob('water-stream-*/params.pickle')]
            report['native_file_metrics'] = [json.loads(p.read_text())
                for p in Path(work).glob('native-fluid-*.metrics.json')]
            report['native_temporary_payloads_cleaned'] = not (
                list(Path(work).glob('native-fluid-*.cbor'))+
                list(Path(work).glob('native-fluid-spool-*')))
            assert report['native_temporary_payloads_cleaned']
        if domain and not args.dry:
            gaia = Path(cloth.cloth_next.water_flow_container)
            report['prepared_gaia'] = str(gaia)
            report['prepared_gaia_bytes'] = gaia.stat().st_size
            if args.verify_reuse:
                stamp=(gaia.stat().st_size,gaia.stat().st_mtime_ns,
                       hashlib.sha256(gaia.read_bytes()).hexdigest())
                assert bpy.ops.clothnext.bake('EXEC_DEFAULT') in ({'FINISHED'},{'RUNNING_MODAL'})
                wait_for_bake()
                boundary,cache=active_cache(cloth)
                assert stamp==(gaia.stat().st_size,gaia.stat().st_mtime_ns,
                               hashlib.sha256(gaia.read_bytes()).hexdigest())
                report['gaia_reused_without_rewrite']=True
            if args.verify_corruption:
                good_cache_hash=hashlib.sha256(cache.read_bytes()).hexdigest()
                corrupt=args.output/'corrupt-copy.gaia'
                changed=False
                with zipfile.ZipFile(gaia) as src, zipfile.ZipFile(corrupt,'w') as dst:
                    for member in src.infolist():
                        data=src.read(member.filename)
                        if not changed and member.filename.endswith('/velocity.npy'):
                            data=data[:-1]+bytes([data[-1]^1]);changed=True
                        dst.writestr(member,data)
                assert changed
                corrupt_hash=hashlib.sha256(corrupt.read_bytes()).hexdigest()
                cloth.cloth_next.water_flow_container=str(corrupt)
                assert bpy.ops.clothnext.bake('EXEC_DEFAULT') in ({'FINISHED'},{'RUNNING_MODAL'})
                try:
                    wait_for_bake()
                except AssertionError:
                    snapshot=solver_test.shared_controller.snapshot()
                    assert snapshot.state.value=='ERROR'
                    assert any(word in (snapshot.error_summary+snapshot.error_details).lower()
                               for word in ('checksum','corrupt'))
                    report['corruption_expected_error']=snapshot.error_summary
                else:
                    raise AssertionError('Corrupt .gaia was accepted')
                assert active_cache(cloth)[1]==cache
                assert hashlib.sha256(cache.read_bytes()).hexdigest()==good_cache_hash
                assert hashlib.sha256(corrupt.read_bytes()).hexdigest()==corrupt_hash
                report['corrupt_gaia_rejected_old_pc2_preserved']=True
                report['result']='PASS'
        if args.lifecycle:
            gates = []
            assert bpy.ops.clothnext.bake('EXEC_DEFAULT') == {'FINISHED'}
            wait_for_bake()
            rebound, cache = active_cache(cloth)
            assert rebound == boundary
            gates.append('rebake_same_boundary')
            assert bpy.ops.clothnext.solver_test_clear() == {'FINISHED'}
            assert not cache.exists()
            assert solver_test.is_unbaked_simulation_modifier(cloth, boundary)
            gates.append('clear_releases_pc2')
            assert bpy.ops.clothnext.bake('EXEC_DEFAULT') == {'FINISHED'}
            wait_for_bake()
            boundary, cache = active_cache(cloth)
            gates.append('bake_after_clear')
            name = cloth.name
            blend = args.output / 'regular-bake-lifecycle.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(blend))
            cloth = bpy.data.objects[name]
            bpy.context.view_layer.objects.active = cloth
            cloth.select_set(True)
            boundary, cache = active_cache(cloth)
            gates.append('save_reload_cache')
            assert bpy.ops.clothnext.solver_test_clear() == {'FINISHED'}
            assert not cache.exists()
            assert solver_test.is_unbaked_simulation_modifier(cloth, boundary)
            gates.append('clear_after_reload')
            report['lifecycle_gates'] = gates
    except BaseException as exc:
        report.update(result='FAIL', exception=repr(exc))
        raise
    finally:
        unsubscribe_trace()
        from cloth_next.ppf.water_stream_worker import peak_working_set_bytes
        report['blender_peak_working_set_bytes'] = peak_working_set_bytes()
        if args.fluid_source:
            report['source_blend_unchanged'] = hashlib.sha256(args.fluid_source.read_bytes()).hexdigest()==source_hash
            report['source_cache_unchanged'] = all(source_stamp(path)==stamp
                                                 for path,stamp in source_stamps.items())
            report['source_cache_files_checked'] = len(source_stamps)
            if not report['source_blend_unchanged'] or not report['source_cache_unchanged']:
                report.update(result='FAIL',exception='Source files changed during the validation run')
        report['wall_seconds'] = time.monotonic() - started
        (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)
        addon_utils.disable('cloth_next')


if __name__ == '__main__':
    main()
