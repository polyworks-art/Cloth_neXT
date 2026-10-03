# SPDX-License-Identifier: GPL-3.0-or-later
"""Read baked FLIP mesh attributes; generic GAIA adapter knows no FLIP objects."""
import hashlib
import json
from pathlib import Path
import bpy
import numpy as np
from ..gaia.water_field import reconstruct, grid_plan, fingerprint, display_stride
from ..gaia.container import GaiaContainer, write_water_container
from ..gaia.solver_fields import official_grid_payload


WATER_COORDINATE_RECIPE = 'flip-evaluated-world-v2'


def flip_sample_to_world(positions, velocities, matrix_world):
    """FLIP mesh-local points; baked world-axis physical m/s vectors.

    The FLIP loader shifts/scales the *mesh* and writes velocity attributes
    unchanged. Translation, display rotation and display scale never apply
    to these already exported physical velocity components.
    """
    p, v, matrix = np.asarray(positions, float), np.asarray(velocities, float), np.asarray(matrix_world, float)
    if (p.ndim != 2 or p.shape[1] != 3 or v.shape != p.shape or matrix.shape != (4,4)
            or not np.isfinite(p).all() or not np.isfinite(v).all() or not np.isfinite(matrix).all()):
        raise ValueError('Non-finite or invalid FLIP position/velocity/transform')
    return p @ matrix[:3,:3].T + matrix[:3,3], v.copy()


class FLIPWaterFlowProvider:
    def __init__(self, context, domain, frames, *, hash_sources=True):
        from mathutils import Vector
        if (domain is None or getattr(getattr(domain, 'flip_fluid', None),
                                     'object_type', '') != 'TYPE_DOMAIN'):
            raise ValueError('Select a baked FLIP Fluids Domain')
        self.context, self.domain = context, domain
        self.properties = domain.flip_fluid.domain
        d = self.properties
        self.root = Path(d.cache.get_cache_abspath()) / 'bakefiles'
        if not self.root.is_dir(): raise ValueError('FLIP cache is missing or not baked')
        export = self.root.parent / 'export' / 'flipdata.sim'
        if not export.is_file(): raise ValueError('FLIP bake timing metadata is missing')
        timing = json.loads(export.read_text(encoding='utf-8'))['domain_data']['simulation']
        rate = timing['frames_per_second']
        if rate.get('is_animated'):
            raise ValueError('Animated FLIP frame rates are not supported for Water Flow')
        baked_fps = float(rate['data'])
        cloth_fps = context.scene.render.fps / context.scene.render.fps_base
        if not np.isfinite(baked_fps) or abs(baked_fps-cloth_fps)>1e-6:
            raise ValueError('FLIP bake FPS differs from Cloth FPS; explicit retiming is not supported yet')
        # Actual baked files are authoritative even if the .blend was last
        # saved before enabling velocity export for a completed rebake.
        first = next(iter(frames))
        if (self.root / f'fluidparticlesvelocity{first:06d}.ffp3').is_file():
            self.mode, self.cache = 'FLUID_PARTICLES', d.mesh_cache.particles
            self.position_pattern, self.velocity_pattern = 'fluidparticles{:06d}.ffp3', 'fluidparticlesvelocity{:06d}.ffp3'
        elif (self.root / f'velocity{first:06d}.bobj').is_file():
            self.mode, self.cache = 'SURFACE_VELOCITY', d.mesh_cache.surface
            self.position_pattern, self.velocity_pattern = '{:06d}.bobj', 'velocity{:06d}.bobj'
        else:
            raise ValueError('FLIP velocity data is not available. Enable Fluid Particle Velocity Attributes and rebake FLIP.')
        matrix = np.asarray(domain.matrix_world, float)
        if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix[:3,:3])) < 1e-12:
            raise ValueError('Invalid FLIP Domain transform')
        self.identity = {'mode': self.mode, 'cache': str(self.root.resolve()),
                         'domain': domain.name, 'matrix': matrix.tolist(), 'sources': [],
                         'coordinate_recipe': WATER_COORDINATE_RECIPE,
                         'baked_fps': baked_fps, 'time_scale': timing.get('time_scale'),
                         'sampling': 'all-liquid-particles' if self.mode == 'FLUID_PARTICLES' else 'surface-only'}
        # Hash both positions and velocity: a same-size overwrite must invalidate.
        self.source_paths = []
        for frame in frames:
            for pattern in (self.position_pattern, self.velocity_pattern):
                path = self.root / pattern.format(frame)
                if not path.is_file(): raise ValueError(f'FLIP requested frame/velocity is missing: {path.name}')
                self.source_paths.append(path)
                if not hash_sources: continue
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b''): digest.update(chunk)
                self.identity['sources'].append([path.name, digest.hexdigest()])
        corners = np.asarray([tuple(domain.matrix_world @ Vector(p)) for p in domain.bound_box])
        # A domain transform changed after baking can leave the loader's
        # parenting decomposition pending on its first import. Settle it with
        # one evaluated import before taking the authoritative grid transform.
        self._load_frame(first)
        obj = self._load_frame(first)
        self.cache_matrix = np.asarray(obj.matrix_world, float).copy()
        # The displayed FLIP cache can extend beyond the domain after a
        # post-bake rotation. Cover its actual transformed cached grid too.
        b = self.cache.bounds
        cache_corners = np.asarray([(x,y,z) for x in (0.,b.width)
            for y in (0.,b.height) for z in (0.,b.depth)])
        cache_world, _ = flip_sample_to_world(cache_corners, np.zeros_like(cache_corners), self.cache_matrix)
        all_corners = np.concatenate((corners,cache_world))
        self.minimum, self.maximum = all_corners.min(axis=0), all_corners.max(axis=0)
        self.identity['cache_matrix'] = self.cache_matrix.tolist()
        self.identity['bounds'] = [self.minimum.tolist(), self.maximum.tolist()]

    def _load_frame(self, frame):
        self.context.scene.frame_set(frame)
        # FLIP's frame handler changes cache parenting/transforms. Evaluate
        # those before a forced import, then evaluate that import as well.
        self.context.view_layer.update()
        previous = self.cache.enable_velocity_attribute
        percentages = ('ffp3_surface_import_percentage', 'ffp3_boundary_import_percentage',
                       'ffp3_interior_import_percentage') if self.mode == 'FLUID_PARTICLES' else ()
        saved_percentages = {name: getattr(self.cache, name) for name in percentages}
        try:
            # Display/import option only, restored immediately; neither bake
            # settings nor source cache is written.
            self.cache.enable_velocity_attribute = True
            for name in percentages: setattr(self.cache, name, 1.0)
            self.cache.load_frame(frame, force_load=True, depsgraph=self.context.evaluated_depsgraph_get())
            self.context.view_layer.update()
        finally:
            self.cache.enable_velocity_attribute = previous
            for name, value in saved_percentages.items(): setattr(self.cache, name, value)
        obj = self.cache.get_cache_object()
        if obj is None or obj.type != 'MESH': raise ValueError('FLIP fluid cache object is unavailable')
        return obj

    def sample(self, frame):
        obj = self._load_frame(frame)
        if not np.allclose(np.asarray(obj.matrix_world), self.cache_matrix, rtol=0., atol=1e-5):
            raise ValueError('FLIP cache transform changes across frames; Water Flow requires fixed grid bounds')
        mesh = obj.data
        attr = mesh.attributes.get('flip_velocity')
        if attr is None or attr.domain != 'POINT' or attr.data_type != 'FLOAT_VECTOR':
            raise ValueError(f'FLIP flip_velocity is missing at frame {frame}; rebake with Velocity Attributes')
        count = len(mesh.vertices)
        if not count or len(attr.data) != count: raise ValueError(f'No usable fluid samples at frame {frame}')
        p, v = np.empty(count*3, np.float32), np.empty(count*3, np.float32)
        mesh.vertices.foreach_get('co', p)
        attr.data.foreach_get('vector', v)
        matrix = np.asarray(obj.matrix_world, float)
        p, v = flip_sample_to_world(p.reshape(-1,3), v.reshape(-1,3), matrix)
        # FLIP documents flip_velocity as physical world velocity in m/s;
        # its loader writes attribute data directly. Do not translate or
        # multiply by the cache object's display scale a second time.
        return p, v


def water_settings_record(obj):
    s = obj.cloth_next
    if not getattr(s, 'water_flow_enabled', False): return None
    domain = s.water_flow_domain
    source = None
    if domain is not None and hasattr(domain, 'flip_fluid') and domain.flip_fluid.object_type == 'TYPE_DOMAIN':
        cache = Path(domain.flip_fluid.domain.cache.get_cache_abspath())
        stats = cache / 'flipstats.data'
        source = {'cache': str(cache), 'matrix': [list(row) for row in domain.matrix_world],
                  'bake_stat': [stats.stat().st_size, stats.stat().st_mtime_ns] if stats.is_file() else None}
    return {'domain': domain.name if domain else None, 'source': source, 'influence': s.water_flow_influence,
            'velocity_scale': s.water_flow_velocity_scale, 'resolution': s.water_flow_resolution,
            'custom_resolution':getattr(s,'water_flow_custom_resolution',100),
            'container': s.water_flow_container}


def water_grid_plan(provider, settings):
    source_voxel=None
    dx=getattr(getattr(getattr(provider,'cache',None),'bounds',None),'dx',None)
    if dx is not None:
        scale=float(np.linalg.norm(provider.cache_matrix[:3,:3],axis=0).min())
        source_voxel=float(dx)*scale
    return grid_plan(provider.minimum,provider.maximum,settings.water_flow_resolution or 'AUTO',
                     getattr(settings,'water_flow_custom_resolution',100),source_voxel_size=source_voxel)


def water_container_destination(value, object_name):
    """Accept a destination folder or a new filename; no existing file needed."""
    path = Path(bpy.path.abspath(value))
    if path.is_dir() or value.endswith(('/', '\\')):
        safe_name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in object_name)
        path = path / f'{safe_name or "Cloth"}_WaterFlow.gaia'
    elif path.suffix.lower() != '.gaia':
        path = path.with_suffix('.gaia')
    return path


def prepare_container(context, obj, first, last):
    from . import water_preparation
    ready = water_preparation.prepared_container(obj, first, last)
    if ready is not None:
        return ready
    s = obj.cloth_next
    if not s.water_flow_container:
        raise ValueError('Choose an output folder or new .gaia filename; the file is created by Prepare')
    path = water_container_destination(s.water_flow_container, obj.name)
    if Path(bpy.path.abspath(s.water_flow_container)) != path:
        s.water_flow_container = str(path)
    frames = range(int(first), int(last)+1)
    provider = FLIPWaterFlowProvider(context, s.water_flow_domain, frames)
    fps = context.scene.render.fps / context.scene.render.fps_base
    layout = water_grid_plan(provider,s)
    dims = layout['dimensions']
    metadata = {'provider': provider.identity, 'frames': [first,last], 'fps': fps,
                'dimensions': dims, 'algorithm': 'trilinear-splat-support-v2',
                'field_format': 'gaia-0.23', 'physical_velocity_units': 'm/s', 'grid_layout':layout}
    metadata['fingerprint'] = fingerprint(metadata)
    if path.is_file():
        try:
            with GaiaContainer(path) as cached:
                if cached.manifest['water']['fingerprint'] == metadata['fingerprint']:
                    # Validate all frame checksums before accepting reuse.
                    for index in range(len(cached.manifest['frames'])): cached.frame(index)
                    return path
        except (ValueError, KeyError, OSError):
            raise ValueError('Derived .gaia cache is corrupted; choose a new container path or rebuild it')
    original = context.scene.frame_current
    def sequence():
        for frame in frames:
            positions, velocities = provider.sample(frame)
            field = reconstruct(positions, velocities, provider.minimum, provider.maximum, dims,
                                support_threshold=layout['support_threshold'])
            yield frame, (frame-first)/fps, field
            del field, positions, velocities
    try:
        write_water_container(path, metadata, sequence())
    finally:
        context.scene.frame_set(original)
    return path


def apply_water_stream_payload(context, payload, resolved, first, last):
    """Export weights and CNX-owned cache identities, never a whole schedule."""
    targets=[obj for obj in context.scene.objects if hasattr(obj,'cloth_next')
             and obj.cloth_next.enabled and obj.cloth_next.role=='CLOTH'
             and getattr(obj.cloth_next,'water_flow_enabled',False)]
    if not targets:return payload,None,''
    from ..ppf.models import ConnectionOwnership
    if (str(getattr(resolved,'protocol_version',''))!='0.23'
            or resolved.ownership is not ConnectionOwnership.OWNED_PROCESS):
        raise ValueError('Water Flow streaming requires a local official GAIA 0.23 installation')
    from ..ppf.schema import envelope
    from ..gaia.streaming import WaterStreamReader
    raw=payload.read_bytes() if isinstance(payload,Path) else payload
    tree=envelope.loads_envelope(raw,envelope.KIND_PARAM,schema_version=2)
    fps=context.scene.render.fps/context.scene.render.fps_base
    descriptor={'version':1,'first':int(first),'last':int(last),'fps':fps,'targets':[]}
    for obj in targets:
        indices=[i for i,(_,names,_) in enumerate(tree['group']) if obj.name in names]
        if len(indices)!=1:raise ValueError('Water Flow target is missing from the solver scene')
        index=indices[0];params,names,_=tree['group'][index]
        if len(names)!=1:raise ValueError('Water Flow requires an individual material group per target')
        path=prepare_container(context,obj,first,last)
        with GaiaContainer(path) as container:digest=container.manifest['water']['fingerprint']
        with WaterStreamReader(path,first=int(first),last=int(last),fps=fps,
                               expected_fingerprint=digest) as reader:
            descriptor['targets'].append({'path':str(Path(path).resolve()),
                'fingerprint':digest,'groups':[index],'dimensions':list(reader.dimensions),
                'influence':obj.cloth_next.water_flow_influence,
                'velocity_scale':obj.cloth_next.water_flow_velocity_scale})
        params['force-field-weight']=1.
    if float(tree['scene'].get('air-density',.001))<=0:
        raise ValueError('Water Flow velocity drag requires positive scene Air Density')
    data=envelope.dumps_envelope(envelope.KIND_PARAM,tree,schema_version=2)
    import json
    return data,hashlib.sha256(data).hexdigest(),json.dumps(descriptor,sort_keys=True,
                                                        separators=(',',':'),allow_nan=False)


def apply_water_payload(context, payload, resolved, first, last):
    """Disabled fast path returns the original payload/hash without FLIP I/O."""
    targets = [obj for obj in context.scene.objects if hasattr(obj, 'cloth_next')
               and obj.cloth_next.enabled and obj.cloth_next.role == 'CLOTH'
               and getattr(obj.cloth_next, 'water_flow_enabled', False)]
    if not targets: return payload, None
    if str(getattr(resolved, 'protocol_version', '')) != '0.23':
        from .solver_test import SceneValidationError
        current = str(getattr(resolved, 'protocol_version', '') or 'unknown')
        raise SceneValidationError(
            f'Water Flow requires official GAIA 0.23; the active solver is {current}. '
            'Open Cloth NeXt Preferences > Solver Installations and select GAIA 0.23. '
            'If it is not installed, use Download Official Solver first.')
    from ..ppf.schema import envelope
    raw = payload.read_bytes() if isinstance(payload, Path) else payload
    tree = envelope.loads_envelope(raw, envelope.KIND_PARAM, schema_version=2)
    grids = []
    for obj in targets:
        indices = [i for i, (params, names, uuids) in enumerate(tree['group']) if obj.name in names]
        if len(indices) != 1: raise ValueError('Water Flow target is not present in the solver scene')
        index = indices[0]
        params, names, _ = tree['group'][index]
        if len(names) != 1:
            raise ValueError('Water Flow currently requires an individual solver material group per target')
        path = prepare_container(context, obj, first, last)
        with GaiaContainer(path) as container:
            field = official_grid_payload(container, [index], influence=obj.cloth_next.water_flow_influence,
                                          velocity_scale=obj.cloth_next.water_flow_velocity_scale)
        grids.extend(field['grids'])
        params['force-field-weight'] = 1.0
    # Keep the existing ambient drag coefficients: zero contribution/outside
    # water then reproduces the ordinary simulation, including its air damping.
    if float(tree['scene'].get('air-density', .001)) <= 0:
        raise ValueError('Water Flow velocity drag requires positive scene Air Density')
    existing = tree.setdefault('force_field', {'grids': [], 'scripts': []})
    existing.setdefault('grids', []).extend(grids)
    from ..gaia.solver_fields import MAX_UPLOAD_BYTES
    total_bytes = sum(int(np.prod(g['shape'])) * 4 for g in existing['grids'])
    if total_bytes > MAX_UPLOAD_BYTES:
        raise ValueError('Combined Water Flow fields exceed the 32 MiB schedule upload budget')
    data = envelope.dumps_envelope(envelope.KIND_PARAM, tree, schema_version=2)
    return data, hashlib.sha256(data).hexdigest()


class CLOTHNEXT_OT_prepare_water_flow(bpy.types.Operator):
    bl_idname = 'cloth_next.prepare_water_flow'
    bl_label = 'Prepare .gaia Water Field'
    _job_id = ''
    @classmethod
    def poll(cls, context):
        from ..bake.controller import shared_controller
        return CLOTHNEXT_PT_water_flow.poll(context) and not shared_controller.snapshot().active
    def execute(self, context):
        from . import water_preparation
        try:
            obj = context.object
            self._job_id = water_preparation.start(context, (obj,),
                obj.cloth_next.bake_start, obj.cloth_next.bake_end)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}
    def modal(self, context, event):
        from . import water_preparation
        from ..bake.controller import shared_controller
        from ..bake.status import BakeState
        if not water_preparation.active(self._job_id):
            state = shared_controller.snapshot().state
            return {'FINISHED'} if state is BakeState.FINISHED else {'CANCELLED'}
        if event.type == 'ESC':
            shared_controller.request_cancel()
        return {'RUNNING_MODAL'}
    def cancel(self, context):
        from . import water_preparation
        water_preparation.cancel(self._job_id)


class CLOTHNEXT_PT_water_flow(bpy.types.Panel):
    bl_label = 'GAIA Water Flow'
    bl_idname = 'CLOTHNEXT_PT_water_flow'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'physics'
    bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context):
        from . import icon_registry
        self.layout.label(text='', **icon_registry.icon_kwargs('gaia_water', 'PHYSICS'))
    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and hasattr(obj, 'cloth_next') and obj.cloth_next.enabled and obj.cloth_next.role == 'CLOTH'
    def draw(self, context):
        from ..bake.controller import shared_controller
        from . import icon_registry
        s, layout = context.object.cloth_next, self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = not shared_controller.snapshot().active
        layout.prop(s, 'water_flow_enabled')
        body = layout.column(align=True)
        body.enabled = s.water_flow_enabled
        for key in ('water_flow_domain', 'water_flow_influence', 'water_flow_velocity_scale',
                    'water_flow_resolution', 'water_flow_container'):
            body.prop(s, key)
            if key == 'water_flow_resolution' and s.water_flow_resolution == 'CUSTOM':
                body.prop(s,'water_flow_custom_resolution')
        if not s.water_flow_container:
            body.label(text='Uses the Cloth cache folder')
        body.separator()
        body.operator('cloth_next.prepare_water_flow', text='Prepare Water Flow',
                      **icon_registry.icon_kwargs('gaia_water', 'PHYSICS'))


class CLOTHNEXT_PT_water_flow_display(bpy.types.Panel):
    bl_label = 'Flow Display'
    bl_idname = 'CLOTHNEXT_PT_water_flow_display'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'physics'
    bl_parent_id = 'CLOTHNEXT_PT_water_flow'
    bl_options = {'DEFAULT_CLOSED'}
    @classmethod
    def poll(cls, context):
        return CLOTHNEXT_PT_water_flow.poll(context)
    def draw(self, context):
        from ..bake.controller import shared_controller
        s, layout = context.object.cloth_next, self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = s.water_flow_enabled and not shared_controller.snapshot().active
        layout.prop(s, 'water_flow_show_vectors')
        body = layout.column(align=True)
        body.enabled = s.water_flow_show_vectors
        if s.water_flow_show_vectors:
            body.prop(s, 'water_flow_debug_mode')
            body.prop(s, 'water_flow_show_bounds')
            body.prop(s, 'water_flow_vector_stride')
            body.prop(s, 'water_flow_vector_scale')
            info = _diagnostics.get(context.object.name)
            if info:
                body.separator()
                body.label(text=f"Frame: {info['frame']}")
                body.label(text='Grid: '+' x '.join(map(str,info['dimensions'])))
                body.label(text=f"Voxel: {info['voxel_size']:.5g} m")
                body.label(text=f"Occupied: {info['occupied_cells']:,} / {info['total_cells']:,}")
                body.label(text=f"Estimated memory: {info['estimated_working_bytes']/1024**2:.1f} MiB")
                body.label(text='Bounds: '+str(info['bounds']))


_handle = None
_preview = {}
_diagnostics = {}


def _draw_vectors(_region=None):
    obj = bpy.context.object
    if obj is None or not hasattr(obj, 'cloth_next'): return
    s = obj.cloth_next
    if not s.water_flow_enabled or not s.water_flow_show_vectors or not s.water_flow_container: return
    path = Path(bpy.path.abspath(s.water_flow_container))
    if not path.is_file(): return
    try:
        key = (str(path), path.stat().st_mtime_ns, bpy.context.scene.frame_current,
               s.water_flow_vector_stride, s.water_flow_vector_scale,
               s.water_flow_influence, s.water_flow_velocity_scale,
               s.water_flow_debug_mode, s.water_flow_show_bounds)
        if key not in _preview:
            from ..gaia.water_field import debug_vectors
            with GaiaContainer(path) as c:
                provider=c.manifest['water'].get('provider', {})
                if provider.get('mode') in {'FLUID_PARTICLES','SURFACE_VELOCITY'}:
                    from ..gaia.water_field import GRID_RECIPE
                    if (provider.get('coordinate_recipe') != WATER_COORDINATE_RECIPE or
                            c.manifest['water'].get('grid_layout',{}).get('recipe') != GRID_RECIPE):
                        return  # Rebuild old derived coordinates/resolution before display.
                records=c.manifest['frames']
                index=min(range(len(records)),key=lambda i:abs(records[i]['frame']-bpy.context.scene.frame_current))
                field=c.frame(index)
                from ..gaia.water_field import debug_samples
                from ..gaia.water_field import grid_diagnostics
                dims=field.velocity.shape[:3][::-1]
                info=records[index].get('diagnostics') or grid_diagnostics(field.minimum,field.maximum,dims)
                info['occupied_cells']=int(np.count_nonzero(field.influence>.01))
                info['frame']=records[index]['frame']
                _diagnostics[obj.name]=info
                stride=display_stride(dims,s.water_flow_vector_stride)
                if s.water_flow_debug_mode == 'POSITIONS':
                    vertices,_=debug_samples(field,stride)
                    primitive='POINTS'
                else:
                    a,b=debug_vectors(field,stride,s.water_flow_vector_scale,
                        mode=s.water_flow_debug_mode,
                        physical_scale=s.water_flow_influence*s.water_flow_velocity_scale)
                    vertices=np.stack((a,b),axis=1).reshape(-1,3)
                    primitive='LINES'
                bounds_vertices=[]
                if s.water_flow_show_bounds:
                    corners=np.asarray([(x,y,z) for x in (field.minimum[0],field.maximum[0])
                        for y in (field.minimum[1],field.maximum[1]) for z in (field.minimum[2],field.maximum[2])])
                    bounds_vertices=np.asarray([corners[i] for a in range(8) for bit in (1,2,4)
                        if a < (a^bit) for i in (a,a^bit)])
            _preview.clear()
            # GPU vertex buffers require 32-bit floats. NumPy float64 buffers
            # can be reinterpreted as float32 components by Blender's upload.
            _preview[key]=(primitive,np.ascontiguousarray(vertices,dtype=np.float32),
                           np.ascontiguousarray(bounds_vertices,dtype=np.float32))
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader=gpu.shader.from_builtin('UNIFORM_COLOR')
        primitive,vertices,bounds_vertices=_preview[key]
        region=_region or bpy.context.region_data
        if region is None: return
        # Explicit world-space matrices; do not inherit another overlay's model transform.
        with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
            gpu.matrix.load_matrix(region.view_matrix)
            gpu.matrix.load_projection_matrix(region.window_matrix)
            shader.bind()
            shader.uniform_float('color',(.1,.6,1.,1.))
            if len(vertices):
                if primitive == 'POINTS': gpu.state.point_size_set(3.)
                batch_for_shader(shader,primitive,{'pos':vertices}).draw(shader)
                if primitive == 'POINTS': gpu.state.point_size_set(1.)
            if len(bounds_vertices):
                shader.uniform_float('color',(1.,.6,.1,1.))
                batch_for_shader(shader,'LINES',{'pos':bounds_vertices}).draw(shader)
    except (OSError,ValueError,KeyError):
        return


def register():
    global _handle
    if (_handle is None and not getattr(bpy.app, 'background', False)
            and hasattr(bpy.types, 'SpaceView3D')):
        _handle=bpy.types.SpaceView3D.draw_handler_add(_draw_vectors,(),'WINDOW','POST_VIEW')


def unregister():
    global _handle
    from . import water_preparation
    water_preparation.shutdown()
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle,'WINDOW')
        _handle=None
    _preview.clear()
    _diagnostics.clear()


CLASSES = (CLOTHNEXT_OT_prepare_water_flow, CLOTHNEXT_PT_water_flow, CLOTHNEXT_PT_water_flow_display)
