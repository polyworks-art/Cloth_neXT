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
from mathutils.bvhtree import BVHTree

import addon_utils
import bpy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lifecycle', action='store_true')
    parser.add_argument('--vertices', type=int, choices=[1000,5000,20000,50000], required=True)
    parser.add_argument('--strength', type=float, default=.2)
    parser.add_argument('--field-resolution', type=int, default=100)
    parser.add_argument('--existing-scene', action='store_true')
    parser.add_argument('--exclude-collider', action='append', default=[])
    parser.add_argument('--preflight-only',action='store_true')
    parser.add_argument('--profile-capture', action='store_true')
    parser.add_argument('--combination', choices=['base','collider','sweep','zones','two','folded'], default='base')
    parser.add_argument('--rebake', action='store_true')
    parser.add_argument('--time-step', type=float)
    parser.add_argument('--cg-max-iter', type=int)
    parser.add_argument('--cg-tol', type=float)
    parser.add_argument('--verify-convergence-failure', action='store_true')
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
    from tools.gaia026_geometry_checks import triangles_pierce, acceleration_max
    addon_utils.enable('cloth_next', default_set=True)
    from cloth_next.blender import solver_test
    from cloth_next.ppf.resolver import SolverResolver, SolverResolutionContext
    from cloth_next.bake.pc2 import read_header, iter_frames
    from cloth_next.ppf.layout import BundledSolverLayout
    layout=BundledSolverLayout.from_executable(args.solver.resolve())
    resolved = SolverResolver(solver_test._version_probe).resolve(
        SolverResolutionContext(managed_root=layout.root_directory))
    if resolved is None:
        raise RuntimeError('The requested real solver is unavailable')
    assert resolved.executable_path.resolve()==args.solver.resolve()
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
        if args.existing_scene:
            if not any(r.module=='api_portal_polyhammer_com' for r in repos):
                repos.new(name='Character scene validation',module='api_portal_polyhammer_com')
            addon_utils.enable('bl_ext.api_portal_polyhammer_com.character_dna',default_set=True)
            addon_utils.enable('bl_ext.api_portal_polyhammer_com.character_assembly',default_set=True)
        bpy.ops.wm.open_mainfile(filepath=str(args.fluid_source.resolve()))
        if args.existing_scene:
            from bl_ext.api_portal_polyhammer_com.character_dna.runtime import controller,engine
            controller.register()
            controller.after_load()
            assert engine.NAMESPACE in bpy.app.driver_namespace, 'Character DNA driver callback unavailable'
            for obj in bpy.context.scene.objects:
                if obj.animation_data:
                    for curve in obj.animation_data.drivers:
                        if engine.NAMESPACE in curve.driver.expression:
                            curve.driver.expression=curve.driver.expression
                    obj.update_tag()
            bpy.context.scene.frame_set(args.first+1)
            bpy.context.scene.frame_set(args.first)
            invalid=[o.name for o in bpy.context.scene.objects if o.animation_data
                and any(engine.NAMESPACE in c.driver.expression and not c.driver.is_valid
                        for c in o.animation_data.drivers)]
            assert not invalid, f'Invalid Character DNA drivers: {invalid}'
        for name in args.exclude_collider:
            excluded=bpy.context.scene.objects.get(name)
            assert excluded is not None, f'Missing excluded object {name}; scene objects: {list(bpy.context.scene.objects.keys())}'
            excluded.cloth_next.enabled=False
        for obj in bpy.context.scene.objects:
            if not args.existing_scene:
                obj.cloth_next.enabled = False
        domains=[obj for obj in bpy.context.scene.objects
                 if getattr(getattr(obj,'flip_fluid',None),'object_type','')=='TYPE_DOMAIN']
        assert len(domains)==1
        domain=domains[0]
        domain.flip_fluid.domain.cache.cache_directory=bpy.path.abspath(domain.flip_fluid.domain.cache.cache_directory)
        from cloth_next.blender.water_flow import FLIPWaterFlowProvider
        provider=FLIPWaterFlowProvider(bpy.context,domain,range(args.first,args.last+1))
        from cloth_next.gaia.preparation import source_stamp
        source_stamps={path:source_stamp(path) for path in provider.source_paths}
        if args.existing_scene:
            positions,velocities=next(provider.sample_stream(args.first).chunks())
        else:
            positions,velocities=provider.sample(args.first)
        assert len(positions)>0
        center=positions[int(np.argmax(np.linalg.norm(velocities,axis=1)))].tolist()
        del positions,velocities,provider
        bpy.context.scene.use_gravity = True
    else:
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)
    nx,ny={1000:(25,40),5000:(50,100),20000:(100,200),50000:(200,250)}[args.vertices]
    objects=[]
    initial={}
    initial_matrices={}
    pin_indices={}
    edge_indices={}
    if args.existing_scene:
        bpy.context.scene.frame_set(args.first)
        objects=[o for o in bpy.context.scene.objects if o.type=='MESH'
                 and o.cloth_next.enabled and o.cloth_next.role=='CLOTH']
        assert objects, 'Source scene has no enabled Cloth objects'
        for obj in objects:
            settings=obj.cloth_next
            settings.bake_start=args.first;settings.bake_end=args.last
            settings.cache_directory=str(args.output)
            settings.water_flow_enabled=not args.dry;settings.water_flow_domain=domain
            settings.water_flow_container=str(args.gaia or (args.output/'Fluid.gaia'))
            settings.water_flow_resolution='100' if args.field_resolution==100 else 'CUSTOM'
            if args.field_resolution!=100:settings.water_flow_custom_resolution=args.field_resolution
            settings.water_flow_coupling_strength=args.strength;settings.water_flow_native_flow=1.
            initial[obj.name]=np.asarray([tuple(v.co) for v in obj.data.vertices],dtype=float)
            initial_matrices[obj.name]=np.asarray(obj.matrix_world,float).copy()
            group=obj.vertex_groups.get(settings.pin_group) if settings.pinning_enabled else None
            pin_indices[obj.name]=[v.index for v in obj.data.vertices if group is not None
                and any(g.group==group.index and g.weight>=.999 for g in v.groups)]
            edge_indices[obj.name]=np.asarray([tuple(e.vertices) for e in obj.data.edges],dtype=int)
    for index in range(0 if args.existing_scene else (2 if args.combination=='two' else 1)):
        verts=[]
        for j in range(ny):
            v=j/(ny-1)
            for i in range(nx):
                u=i/(nx-1)
                if args.combination=='folded':
                    # Hairpin: initially separated layers, continuous rounded fold.
                    angle=np.pi*v
                    xyz=((u-.5)*.6, .3*np.sin(angle), .15*np.cos(angle))
                else:
                    xyz=((u-.5)*.6,(v-.5)*.6,.01*np.sin(2*np.pi*u)*np.sin(2*np.pi*v))
                verts.append(xyz)
        faces=[]
        for j in range(ny-1):
            for i in range(nx-1):
                k=j*nx+i
                faces.extend(((k,k+1,k+nx+1),(k,k+nx+1,k+nx)))
        mesh=bpy.data.meshes.new(f'ProductionGrid{args.vertices}_{index}')
        mesh.from_pydata(verts,[],faces);mesh.update()
        cloth=bpy.data.objects.new(mesh.name,mesh)
        bpy.context.collection.objects.link(cloth)
        cloth.location=(center[0],center[1],center[2]+.05+index*.025)
        bpy.ops.object.select_all(action='DESELECT')
        cloth.select_set(True);bpy.context.view_layer.objects.active=cloth
        assert bpy.ops.clothnext.quick_assign_role('EXEC_DEFAULT',role='CLOTH')=={'FINISHED'}
        settings=cloth.cloth_next
        settings.bake_start=args.first;settings.bake_end=args.last
        settings.cache_directory=str(args.output)
        settings.collision.enabled=True
        settings.collision.collision_gap=.0005
        settings.pinning_enabled=True
        group=cloth.vertex_groups.new(name='Pinned edge')
        pins=list(range(nx))
        group.add(pins,1.,'REPLACE')
        settings.pin_group=group.name;settings.pin_mode='STATIC'
        settings.pin_constraint_type='HARD'
        if domain:
            settings.water_flow_enabled=not args.dry
            settings.water_flow_domain=domain
            settings.water_flow_container=str(args.gaia or (args.output/'Fluid.gaia'))
            settings.water_flow_resolution='100' if args.field_resolution == 100 else 'CUSTOM'
            if args.field_resolution != 100:
                settings.water_flow_custom_resolution=args.field_resolution
            settings.water_flow_coupling_strength=args.strength
            settings.water_flow_native_flow=1.
        if args.combination=='zones':
            from cloth_next.blender import material_zones
            assert bpy.ops.clothnext.add_material_zone()=={'FINISHED'}
            zone=settings.material_zones[-1]
            zone.name='Reinforced high-friction half'
            for key,value in [('young-mod',2000.),('friction',.8)]:
                override=zone.overrides.add();override.key=key;override.value=value
            owners=[zone.token if sum(mesh.vertices[i].co.x for i in face.vertices)>0 else 0
                    for face in mesh.polygons]
            material_zones.write_owners(cloth,owners)
        objects.append(cloth)
        initial[cloth.name]=np.asarray(verts,dtype=np.float64)
        pin_indices[cloth.name]=pins
        edge_indices[cloth.name]=np.asarray([tuple(e.vertices) for e in mesh.edges],dtype=int)
    if args.combination in ('collider','sweep','zones'):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16,ring_count=8,radius=.12,
            location=(center[0],center[1],center[2]-.2))
        collider=bpy.context.object
        assert bpy.ops.clothnext.quick_assign_role('EXEC_DEFAULT',role='COLLIDER')=={'FINISHED'}
        collider.cloth_next.collider_motion='ANIMATED'
        if args.combination in ('sweep','zones'):
            poses=[(0.,-.2),(-.18,-.15),(-.42,-.25),(-.18,-.35)]
            for offset in range(0,args.last-args.first+13,12):
                y,z=poses[(offset//12)%4]
                collider.location=(center[0],center[1]+y,center[2]+z)
                collider.keyframe_insert(data_path='location',frame=args.first+offset)
        else:
            collider.keyframe_insert(data_path='location',frame=args.first)
            collider.location.z+=.15
            collider.keyframe_insert(data_path='location',frame=max(args.first+1,args.last))
    bpy.ops.object.select_all(action='DESELECT')
    cloth=objects[0];cloth.select_set(True);bpy.context.view_layer.objects.active=cloth
    if not domain:bpy.context.scene.render.fps=24
    fps=bpy.context.scene.render.fps/bpy.context.scene.render.fps_base
    quality=bpy.context.scene.cloth_next_quality
    for name,value in [('time_step',args.time_step),('cg_max_iter',args.cg_max_iter),('cg_tol',args.cg_tol)]:
        if value is not None:setattr(quality,name,value)
    bpy.context.scene.frame_set(args.first)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.output/'fixture.blend'),check_existing=False)
    frame_count = args.last-args.first+1
    report = {'result': 'RUNNING', 'solver': str(resolved.executable_path),
              'blender': bpy.app.version_string, 'frames': frame_count, 'vertices':sum(len(o.data.vertices) for o in objects) if args.existing_scene else args.vertices, 'total_cloth_vertices':sum(len(o.data.vertices) for o in objects),
              'combination':args.combination,'coupling_strength':args.strength,'self_collision_enabled':True,
              'source_range':[args.first,args.last], 'dry':args.dry, 'center':center,
              'entry_point': 'bpy.ops.clothnext.bake(EXEC_DEFAULT)',
              'headless_timer_pump': True,'fps':fps}
    report['quality']={name:getattr(quality,name) for name in ['time_step','cg_max_iter','cg_tol']}
    if args.existing_scene:
        report['geometry_source']='Existing scene meshes and enabled collider roles'
        report['excluded_colliders']=args.exclude_collider
        report['scene_objects']=[dict(name=o.name,role=o.cloth_next.role,
            vertices=len(o.data.vertices),pinning_enabled=o.cloth_next.pinning_enabled,
            self_collision_enabled=o.cloth_next.collision.enabled)
            for o in bpy.context.scene.objects if o.type=='MESH' and o.cloth_next.enabled]
        report['self_collision_enabled']=all(o.cloth_next.collision.enabled for o in objects)
    preferences=bpy.context.preferences.addons['cloth_next'].preferences
    preferences.auto_cancel_high_ram=True
    preferences.auto_cancel_ram_percent=90
    if args.preflight_only:
        report['result']='PREFLIGHT_PASS'
        report['native_solve_executed']=False
        report['source_blend_unchanged']=source_hash==hashlib.sha256(args.fluid_source.read_bytes()).hexdigest()
        report['invalid_character_drivers']=[o.name for o in bpy.context.scene.objects
            if o.animation_data and any('character_dna_native_solve_v1' in c.driver.expression
                and not c.driver.is_valid for c in o.animation_data.drivers)]
        assert report['source_blend_unchanged']
        assert not report['invalid_character_drivers'], report['invalid_character_drivers']
        (args.output/'report.json').write_text(json.dumps(report,indent=2))
        return
    report['ram_safety']={'enabled':preferences.auto_cancel_high_ram,
                         'threshold_percent':preferences.auto_cancel_ram_percent}
    ram_samples=[]
    def capture_ram():
        current=solver_test.shared_telemetry.snapshot()
        if not ram_samples or ram_samples[-1]['updated_at']!=current.updated_at:
            ram_samples.append(dict(updated_at=current.updated_at,seconds=time.monotonic()-started,
                used_bytes=current.ram_used_bytes,total_bytes=current.ram_total_bytes,stale=current.stale))
            from cloth_next.telemetry.system import WindowsSystemProvider
            _,direct_used,direct_total=WindowsSystemProvider().sample()
            (args.output/'ram-live.json').write_text(json.dumps(dict(
                configured_threshold_fraction=solver_test._ram_auto_cancel.threshold_fraction,
                configured_enabled=solver_test._ram_auto_cancel_enabled,
                direct_used_bytes=direct_used,direct_total_bytes=direct_total,
                recent_samples=ram_samples[-20:]),indent=2))
    started = time.monotonic()
    from cloth_next.ppf_run.session import SolverSession, _SOLVER_METRICS, _tail_numeric_metric
    original_activity = SolverSession._runtime_activity
    original_run = SolverSession._run
    original_merge = solver_test._merge_export_diagnostics
    def capture_export_timings(diagnostics, plan):
        original_merge(diagnostics, plan)
        report['export_timings'] = dict(plan.export_timings)
    solver_test._merge_export_diagnostics = capture_export_timings
    session_results = []
    def capture_run(session, **kwargs):
        try:
            return original_run(session, **kwargs)
        finally:
            session_results.append(dict(timings=dict(session.diagnostics.timings),
                contact_peak=session.diagnostics.contact_peak,
                contact_samples=session.diagnostics.contact_samples))
    SolverSession._run = capture_run
    telemetry = []
    telemetry_paths = []
    def capture_activity(session):
        result = original_activity(session)
        root = session._checkpoint_output_directory()/'data'
        if str(root) not in telemetry_paths:
            telemetry_paths.append(str(root))
        values = {key:_tail_numeric_metric(root/name) for key,name in _SOLVER_METRICS.items()}
        record = dict(seconds=time.monotonic()-started, **values)
        if not telemetry or any(record[key]!=telemetry[-1][key] for key in values):
            telemetry.append(record)
        return result
    SolverSession._runtime_activity = capture_activity
    report['state_transitions'] = []
    report['activity_events'] = []
    def record_state(snapshot):
        capture_ram()
        states = report['state_transitions']
        if not states or states[-1]['state'] != snapshot.state.value:
            states.append({'state':snapshot.state.value,'seconds':time.monotonic()-started})
        activity = dict(state=snapshot.state.value, activity=snapshot.activity_code.value,
            label=snapshot.activity_label, current=snapshot.progress_current,
            total=snapshot.progress_total, eta=snapshot.estimated_remaining_seconds)
        previous = report['activity_events'][-1] if report['activity_events'] else None
        if previous is None or any(previous[k] != v for k,v in activity.items()):
            report['activity_events'].append(dict(seconds=time.monotonic()-started, **activity))
    unsubscribe_trace = solver_test.shared_controller.subscribe(record_state)
    def wait_for_bake():
        deadline = time.monotonic() + args.timeout
        while solver_test.shared_controller.snapshot().active:
            capture_ram()
            if solver_test._active_plan is not None:
                report['export_timings'] = dict(solver_test._active_plan.export_timings)
            if time.monotonic() >= deadline:
                solver_test.request_cancel()
                cancel_deadline = time.monotonic()+120
                while solver_test.shared_controller.snapshot().active and time.monotonic()<cancel_deadline:
                    from cloth_next.blender import water_preparation
                    if water_preparation.active():water_preparation._pump()
                    if solver_test._pin_capture is not None:solver_test._pin_capture_pump()
                    solver_test._startup_pump();solver_test._pump();time.sleep(.05)
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
        assert header.frame_count == frame_count and header.vertex_count == len(obj.data.vertices)
        return modifiers[0], cache

    def validation_frame(frame):
        if not args.existing_scene:
            bpy.context.scene.frame_set(frame)
            return
        import importlib
        module=importlib.import_module(type(domain.flip_fluid.domain.mesh_cache.particles).__module__)
        previous=module.DISABLE_MESH_CACHE_LOAD
        try:
            # Fluid viewport meshes do not participate in completed Cloth PC2 playback.
            # Keep scene time, rig animation and Cloth evaluation fully active.
            module.DISABLE_MESH_CACHE_LOAD=True
            bpy.context.scene.frame_set(frame)
        finally:module.DISABLE_MESH_CACHE_LOAD=previous

    profiler = None
    if args.profile_capture:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    try:
        report['operator_result'] = sorted(bpy.ops.clothnext.bake('EXEC_DEFAULT'))
        snapshot = wait_for_bake()
        report.update(state=snapshot.state.value, error=snapshot.error_summary,
                      error_details=snapshot.error_details)
        checks=[]
        solved_samples={}
        for obj in objects:
            boundary,cache=active_cache(obj)
            samples=np.asarray(list(iter_frames(cache)),dtype=np.float64)
            assert np.isfinite(samples).all(), 'NaN/Inf in native PC2'
            solved_samples[obj.name]=samples
            rest=initial[obj.name];edges=edge_indices[obj.name];pins=pin_indices[obj.name]
            metric_samples=samples;metric_rest=rest
            if args.existing_scene:
                world=[]
                for frame,points in enumerate(samples,args.first):
                    validation_frame(frame)
                    matrix=np.asarray(obj.matrix_world,float)
                    world.append(points@matrix[:3,:3].T+matrix[:3,3])
                metric_samples=np.asarray(world)
                matrix=initial_matrices[obj.name]
                metric_rest=rest@matrix[:3,:3].T+matrix[:3,3]
            rest_lengths=np.linalg.norm(metric_rest[edges[:,0]]-metric_rest[edges[:,1]],axis=1)
            lengths=np.linalg.norm(metric_samples[:,edges[:,0]]-metric_samples[:,edges[:,1]],axis=2)
            max_ratio=float((lengths/rest_lengths).max())
            min_ratio=float((lengths/rest_lengths).min())
            speeds=np.linalg.norm(np.diff(metric_samples,axis=0)*fps,axis=2)
            max_speed=float(speeds.max()) if speeds.size else 0.
            max_pin_error=float(np.linalg.norm(samples[:,pins]-rest[pins],axis=2).max()) if pins else 0.
            displacement=float(np.linalg.norm(metric_samples-metric_rest,axis=2).max())
            obj.data.calc_loop_triangles()
            triangles=np.asarray([tuple(face.vertices) for face in obj.data.loop_triangles],dtype=int)
            area=np.linalg.norm(np.cross(samples[:,triangles[:,1]]-samples[:,triangles[:,0]],
                                        samples[:,triangles[:,2]]-samples[:,triangles[:,0]]),axis=2)
            rest_area=np.linalg.norm(np.cross(rest[triangles[:,1]]-rest[triangles[:,0]],
                                             rest[triangles[:,2]]-rest[triangles[:,0]]),axis=1)
            min_area_ratio=float((area/rest_area).min())
            intersections=[]
            for sample_index in sorted(set([0,len(samples)//2,len(samples)-1])):
                vertices=samples[sample_index]
                tree=BVHTree.FromPolygons(vertices.tolist(),triangles.tolist(),all_triangles=True)
                pairs=[(a,b) for a,b in tree.overlap(tree) if a<b and
                       not set(triangles[a]).intersection(triangles[b])]
                pierced=[(a,b) for a,b in pairs if triangles_pierce(vertices[triangles[a]],vertices[triangles[b]])]
                intersections.append(dict(frame=args.first+sample_index,
                    nonadjacent_overlap_candidates=len(pairs),strict_triangle_piercings=len(pierced)))
            playback_error=0.
            collider_clearance=[]
            for frame,expected in enumerate(samples,args.first):
                validation_frame(frame)
                if args.combination in ('collider','sweep','zones'):
                    world=expected+np.asarray(obj.location)
                    collider_clearance.append(float(np.linalg.norm(world-np.asarray(collider.location),axis=1).min()-.12))
                depsgraph=bpy.context.evaluated_depsgraph_get()
                temporary=None
                if args.existing_scene:
                    cutoff=list(obj.modifiers).index(boundary)
                    temporary=solver_test._make_boundary_evaluation_copy(bpy.context,obj,cutoff)
                    bpy.context.view_layer.update()
                evaluated=(temporary or obj).evaluated_get(depsgraph);mesh=evaluated.to_mesh()
                try:
                    actual=np.empty(len(obj.data.vertices)*3,dtype=np.float32)
                    mesh.vertices.foreach_get('co',actual)
                    playback_error=max(playback_error,float(np.abs(actual.reshape(-1,3)-expected).max()))
                finally:
                    evaluated.to_mesh_clear()
                    if temporary is not None:bpy.data.objects.remove(temporary,do_unlink=True)
            checks.append(dict(object=obj.name,pc2=str(cache),finite=True,
                vertices=len(obj.data.vertices),frames=len(samples),max_speed_m_s=max_speed,
                max_edge_stretch_ratio=max_ratio,min_edge_ratio=min_ratio,
                max_pin_error_m=max_pin_error,max_displacement_m=displacement,
                playback_max_error=playback_error,min_triangle_area_ratio=min_area_ratio,
                max_acceleration_m_s2=acceleration_max(metric_samples,fps),self_intersection_samples=intersections,
                metrics_coordinate_space='Blender world meters' if args.existing_scene else 'unscaled fixture meters'))
            report['objects']=checks
            if collider_clearance:
                checks[-1]['minimum_animated_collider_sphere_clearance_m']=min(collider_clearance)
                # UV sphere is inscribed; allow its known faceting, never alter motion.
                assert min(collider_clearance)>-.012,checks[-1]
            assert playback_error<1e-5, checks[-1]
            assert max_pin_error<.002, checks[-1]
            assert max_speed<100 and max_ratio<4 and min_ratio>1e-5, checks[-1]
            assert min_area_ratio>1e-5 and not any(x['strict_triangle_piercings'] for x in intersections),checks[-1]
        if len(objects)==2:
            cross_checks=[]
            triangles=np.asarray([tuple(face.vertices) for face in objects[0].data.polygons],dtype=int)
            for frame_index in sorted(set([0,frame_count//2,frame_count-1])):
                positions=[solved_samples[o.name][frame_index]+np.asarray(o.location) for o in objects]
                trees=[BVHTree.FromPolygons(p.tolist(),triangles.tolist(),all_triangles=True) for p in positions]
                pairs=trees[0].overlap(trees[1])
                count=sum(triangles_pierce(positions[0][triangles[a]],positions[1][triangles[b]]) for a,b in pairs)
                cross_checks.append(dict(frame=args.first+frame_index,overlap_candidates=len(pairs),strict_piercings=count))
            report['cloth_cloth_intersection_samples']=cross_checks
            assert not any(c['strict_piercings'] for c in cross_checks),cross_checks
        report.update(result='PASS',objects=checks,pc2=checks[0]['pc2'])
        report['final_local_positions']=samples[-1].tolist()
        if args.rebake:
            import shutil
            for index,check in enumerate(checks):
                preserved=args.output/f'validated-before-rebake-{index}.pc2'
                shutil.copyfile(check['pc2'],preserved)
                check['pc2']=str(preserved)
            report['pc2']=checks[0]['pc2']
            for obj in objects: obj.cloth_next.water_flow_coupling_strength=args.strength*1.5
            assert bpy.ops.clothnext.bake('EXEC_DEFAULT') in ({'FINISHED'},{'RUNNING_MODAL'})
            wait_for_bake()
            rebake_checks=[]
            for obj in objects:
                _,new_cache=active_cache(obj)
                rebaked=np.asarray(list(iter_frames(new_cache)))
                assert np.isfinite(rebaked).all()
                speed=float(np.linalg.norm(np.diff(rebaked,axis=0)*fps,axis=2).max())
                error=float(np.linalg.norm(rebaked[:,pin_indices[obj.name]]-initial[obj.name][pin_indices[obj.name]],axis=2).max())
                assert speed<100 and error<.002
                rebake_checks.append(dict(pc2=str(new_cache),max_speed_m_s=speed,max_pin_error_m=error,
                                           actual_strength=args.strength*1.5,finite=True))
            report['rebake_objects']=rebake_checks
            report['changed_settings_rebake']='PASS'
        if args.verify_convergence_failure:
            good=[active_cache(obj)[1] for obj in objects]
            hashes=[hashlib.sha256(path.read_bytes()).hexdigest() for path in good]
            quality.cg_max_iter=100;quality.cg_tol=.00001;quality.time_step=.01
            assert bpy.ops.clothnext.bake('EXEC_DEFAULT') in ({'FINISHED'},{'RUNNING_MODAL'})
            try:wait_for_bake()
            except AssertionError:
                failure=solver_test.shared_controller.snapshot()
                assert failure.state.value=='ERROR'
                assert 'iteration cap' in failure.error_details.lower()
                assert 'PCG Max Iterations' in failure.error_details
                assert hashes==[hashlib.sha256(path.read_bytes()).hexdigest() for path in good]
                assert [active_cache(obj)[1] for obj in objects]==good
                report['expected_convergence_failure']=dict(summary=failure.error_summary,
                    details=failure.error_details,previous_pc2_preserved=True,previous_playback_active=True)
            else:raise AssertionError('Deliberately insufficient convergence budget was accepted')
            report['result']='PASS'
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
        if profiler is not None:
            import pstats
            profiler.disable()
            profiler.dump_stats(str(args.output / 'capture.pstats'))
            with (args.output / 'capture-profile.txt').open('w', encoding='utf-8') as stream:
                pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(50)
        unsubscribe_trace()
        solver_test._merge_export_diagnostics = original_merge
        SolverSession._runtime_activity = original_activity
        SolverSession._run = original_run
        report['session_timings_and_contacts'] = session_results
        report['system_ram_samples']=ram_samples
        report['solver_diagnostics_samples'] = telemetry
        report['solver_diagnostics_paths'] = telemetry_paths
        import inspect
        report['runtime_metric_reader_source'] = inspect.getsource(_tail_numeric_metric)
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
