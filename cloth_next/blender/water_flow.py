# SPDX-License-Identifier: GPL-3.0-or-later
"""Read baked FLIP mesh attributes; generic GAIA adapter knows no FLIP objects."""
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile
import bpy
import numpy as np
from ..gaia.water_field import reconstruct, grid_plan, fingerprint, display_stride
from ..gaia.container import GaiaContainer, write_water_container
from ..gaia.solver_fields import official_grid_payload


WATER_COORDINATE_RECIPE = 'flip-evaluated-world-v2'
_grid_display_cache = {}
# (scene name, frame) -> world (min, max) of Hydrodynamic Pressure cloth,
# recorded during preparation frame changes and consumed once per Bake.
_motion_bounds = {}
# A frame change can cost seconds (FLIP reloads its meshes), so frames the
# capture passes did not visit are sampled sparsely and padded generously.
MAX_BOUNDS_SAMPLES = 25
BOUNDS_MARGIN_M = .25
SAMPLED_BOUNDS_MARGIN_M = .4


def _pressure_targets(scene):
    return [obj for obj in getattr(scene, 'objects', ())
            if getattr(getattr(obj, 'cloth_next', None), 'enabled', False)
            and obj.cloth_next.role == 'CLOTH'
            and getattr(obj.cloth_next, 'water_flow_enabled', False)
            and getattr(obj.cloth_next, 'water_hydrodynamic_pressure', False)]


def _world_box(scene, targets):
    from mathutils import Vector
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in targets:
        evaluated = obj.evaluated_get(depsgraph)
        points.extend(tuple(evaluated.matrix_world @ Vector(c)) for c in evaluated.bound_box)
    points = np.asarray(points, float)
    return points.min(0), points.max(0)


def record_motion_bounds(scene, frame):
    """Cheap when no cloth uses pressure; never raises into a capture pass."""
    try:
        targets = _pressure_targets(scene)
        if targets and float(frame).is_integer():
            _motion_bounds[(scene.name, int(frame))] = _world_box(scene, targets)
    except Exception:  # bounds are re-sampled later if a record is missing
        pass


def hydrodynamic_bounds(context, targets, first, last, progress=None):
    """World box covering the pressure cloth over ``first..last``."""
    scene = context.scene
    frames = range(int(first), int(last) + 1)
    known = [_motion_bounds.get((scene.name, frame)) for frame in frames]
    boxes = [box for box in known if box is not None]
    margin = BOUNDS_MARGIN_M
    missing = [frame for frame, box in zip(frames, known) if box is None]
    if missing:
        margin = SAMPLED_BOUNDS_MARGIN_M
        count = min(MAX_BOUNDS_SAMPLES, len(missing))
        picks = sorted({missing[round(i * (len(missing) - 1) / max(count - 1, 1))]
                        for i in range(count)})
        original = scene.frame_current
        from .preparation_preview import quiet_debug_previews
        try:
            for index, frame in enumerate(picks):
                if progress is not None:
                    progress(index, len(picks))
                with quiet_debug_previews(scene):
                    scene.frame_set(frame)
                boxes.append(_world_box(scene, targets))
        finally:
            with quiet_debug_previews(scene):
                scene.frame_set(original)
    for frame in frames:
        _motion_bounds.pop((scene.name, frame), None)
    lo = np.min([box[0] for box in boxes], axis=0) - margin
    hi = np.max([box[1] for box in boxes], axis=0) + margin
    return np.r_[lo, hi].tolist()
WATER_SOURCE_OPTIONS = (
    ('FLIP_FLUIDS','FLIP Fluids',True,
     'Use a baked FLIP Fluids domain with particle velocity export enabled.'),
    ('MANTAFLOW','Mantaflow',True,
     'Use a baked Blender Mantaflow Liquid domain: Data cache in OpenVDB, Mesh baked.'),
    ('HOUDINI','Houdini FX',True,
     'Export the FLIP particles with their v attribute as Alembic (and the liquid mesh for '
     'Hydrodynamic Pressure), then import them into Blender.'),
    ('LIQUIGEN','LiquiGen',True,
     'Export liquid particles as Alembic with Export velocity enabled (and the liquid mesh for '
     'Hydrodynamic Pressure), then import them into Blender.'),
)


def prepared_grid_info(settings):
    """Read only bounded manifest metadata; never load fields in a UI draw."""
    if not settings.water_flow_container:
        return None
    path=Path(bpy.path.abspath(settings.water_flow_container))
    try:
        stat=path.stat();key=(str(path),stat.st_size,stat.st_mtime_ns)
        if key not in _grid_display_cache:
            with GaiaContainer(path) as cache:
                _grid_display_cache[key]=dict(cache.manifest['water'].get('grid_layout',{}))
            if len(_grid_display_cache)>32:
                newest=_grid_display_cache[key]
                _grid_display_cache.clear();_grid_display_cache[key]=newest
        return _grid_display_cache[key]
    except (OSError,ValueError,KeyError,BadZipFile):
        return None


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
        self.frame_count = len(frames)
        self.properties = domain.flip_fluid.domain
        d = self.properties
        if getattr(getattr(d,'bake',None),'is_simulation_running',False):
            raise ValueError('FLIP is still baking; finish the entire requested range before Fluid Interaction')
        self.root = Path(d.cache.get_cache_abspath()) / 'bakefiles'
        if not self.root.is_dir(): raise ValueError('FLIP cache is missing or not baked')
        export = self.root.parent / 'export' / 'flipdata.sim'
        if not export.is_file(): raise ValueError('FLIP bake timing metadata is missing')
        baked_domain = json.loads(export.read_text(encoding='utf-8'))['domain_data']
        timing = baked_domain['simulation']
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
                         'provider': 'FLIP Fluids',
                         'domain': domain.name, 'matrix': matrix.tolist(), 'sources': [],
                         'coordinate_recipe': 'flip-streamed-world-v3' if self.mode=='FLUID_PARTICLES' else WATER_COORDINATE_RECIPE,
                         'baked_fps': baked_fps, 'time_scale': timing.get('time_scale'),
                         'baked_world_scale': baked_domain.get('initialize',{}).get('scale'),
                         'sampling': 'all-liquid-particles' if self.mode == 'FLUID_PARTICLES' else 'surface-only'}
        import sys
        provider_module = sys.modules.get(type(d).__module__.split('.properties.')[0])
        self.identity['provider_version'] = getattr(provider_module,'bl_info',{}).get('version')
        self.identity['source_object_id'] = getattr(getattr(domain,'cloth_next',None),'uuid','') or domain.name
        # Hash both positions and velocity: a same-size overwrite must invalidate.
        self.source_paths = [export]
        for frame in frames:
            patterns=(self.position_pattern,self.velocity_pattern)
            if self.mode=='FLUID_PARTICLES':patterns+=('bounds{:06d}.bbox',)
            for pattern in patterns:
                path = self.root / pattern.format(frame)
                if not path.is_file(): raise ValueError(f'FLIP requested frame/velocity is missing: {path.name}')
                self.source_paths.append(path)
        if hash_sources:
            for path in self.source_paths:
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b''): digest.update(chunk)
                self.identity['sources'].append([path.name, digest.hexdigest()])
        corners = np.asarray([tuple(domain.matrix_world @ Vector(p)) for p in domain.bound_box])
        # A domain transform changed after baking can leave the loader's
        # parenting decomposition pending on its first import. Settle it with
        # one evaluated import before taking the authoritative grid transform.
        if self.mode=='FLUID_PARTICLES':
            bounds,self.cache_matrix=self._stream_transform(first)
            self.stream_bounds=bounds
        else:
            self._load_frame(first)
            obj=self._load_frame(first)
            self.cache_matrix=np.asarray(obj.matrix_world,float).copy()
        # The displayed FLIP cache can extend beyond the domain after a
        # post-bake rotation. Cover its actual transformed cached grid too.
        spans=tuple(bounds[k] for k in ('width','height','depth')) if self.mode=='FLUID_PARTICLES' else (self.cache.bounds.width,self.cache.bounds.height,self.cache.bounds.depth)
        cache_corners = np.asarray([(x,y,z) for x in (0.,spans[0])
            for y in (0.,spans[1]) for z in (0.,spans[2])])
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
            for name in percentages:setattr(self.cache,name,1.0)
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

    def sample_stream(self,frame):
        if self.mode!='FLUID_PARTICLES':return self.sample(frame)
        from ..gaia.particle_stream import ParticleFileSample
        bounds,matrix=self._stream_transform(frame)
        if not np.allclose(matrix,self.cache_matrix,rtol=0.,atol=1e-5):
            raise ValueError('FLIP cache transform changes across frames; Water Flow requires fixed grid bounds')
        return ParticleFileSample(self.root/self.position_pattern.format(frame),
            self.root/self.velocity_pattern.format(frame),tuple(map(tuple,matrix)),
            tuple(bounds[k] for k in ('x','y','z')))

    def _stream_transform(self,frame):
        """Evaluate the FLIP loader's transform without importing any particle mesh."""
        import importlib
        from mathutils import Matrix,Vector
        module=importlib.import_module(type(self.cache).__module__)
        previous=module.DISABLE_MESH_CACHE_LOAD
        try:
            module.DISABLE_MESH_CACHE_LOAD=True
            from .preparation_preview import frame_set
            frame_set(self.context.scene,frame)
            self.context.view_layer.update()
        finally:module.DISABLE_MESH_CACHE_LOAD=previous
        raw=json.loads((self.root/f'bounds{frame:06d}.bbox').read_text(encoding='utf-8'))
        floats=('x','y','z','width','height','depth','dx')
        bounds={k:float(np.float32(raw[k])) for k in floats}
        if not np.isfinite(list(bounds.values())).all() or min(bounds[k] for k in ('width','height','depth','dx'))<=0:
            raise ValueError('Invalid FLIP cache bounds')
        resolution=max(int(raw[k]) for k in ('isize','jsize','ksize'))
        isize,jsize,ksize,dx=self.properties.simulation.get_viewport_grid_dimensions(resolution=resolution)
        scale=min(isize*dx/bounds['width'],jsize*dx/bounds['height'],ksize*dx/bounds['depth'])
        aabb=module.AABB.from_blender_object(self.domain)
        temporary=bpy.data.objects.new('__ClothNeXtFlowTransform',None)
        try:
            self.context.scene.collection.objects.link(temporary)
            temporary.parent=self.domain
            # The loader performs a second settled import with this inverse already set.
            temporary.matrix_parent_inverse=self.domain.matrix_world.inverted()
            temporary.matrix_world=Matrix.Identity(4)
            temporary.matrix_parent_inverse=self.domain.matrix_world.inverted()
            temporary.scale=(scale,scale,scale)
            temporary.location=Vector((aabb.x,aabb.y,aabb.z))
            self.context.view_layer.update()
            return bounds,np.asarray(temporary.matrix_world,float).copy()
        finally:bpy.data.objects.remove(temporary,do_unlink=True)


def water_provider(context, obj, frames, *, hash_sources=True):
    """The Water Flow source of ``obj`` (FLIP Fluids or Mantaflow)."""
    s = obj.cloth_next
    kind = getattr(s, 'water_flow_provider', 'FLIP_FLUIDS')
    if kind == 'MANTAFLOW':
        from .water_mantaflow import MantaflowWaterFlowProvider
        return MantaflowWaterFlowProvider(context, s.water_flow_domain, frames,
                                          container=s.water_flow_container, hash_sources=hash_sources)
    if kind in PARTICLE_PROVIDERS:
        from .water_particles import ParticleWaterFlowProvider
        return ParticleWaterFlowProvider(
            context, s.water_flow_domain, frames, container=s.water_flow_container,
            surface=s.water_flow_surface, provider=PARTICLE_PROVIDERS[kind], hash_sources=hash_sources,
            need_surface=bool(getattr(s, 'water_hydrodynamic_pressure', False)))
    return FLIPWaterFlowProvider(context, s.water_flow_domain, frames, hash_sources=hash_sources)


PARTICLE_PROVIDERS = {'HOUDINI': 'Houdini FX', 'LIQUIGEN': 'LiquiGen'}


def mantaflow_hint(domain):
    """One line on what a Mantaflow domain still needs, or '' when it is usable."""
    from .water_mantaflow import fluid_domain
    settings = fluid_domain(domain) if domain is not None else None
    if settings is None or settings.domain_type != 'LIQUID':
        return 'Pick a Mantaflow Liquid Domain'
    if settings.cache_data_format != 'OPENVDB':
        return 'Set Cache > Data format to OpenVDB, then bake'
    if not settings.use_mesh:
        return 'Enable Mesh on the Domain, then bake'
    if not (settings.has_cache_baked_data and settings.has_cache_baked_mesh):
        return 'Bake the Mantaflow Data and Mesh'
    return ''


def water_settings_record(obj):
    s = obj.cloth_next
    if not getattr(s, 'water_flow_enabled', False): return None
    domain = s.water_flow_domain
    source = None
    if getattr(s, 'water_flow_provider', 'FLIP_FLUIDS') == 'MANTAFLOW' and domain is not None:
        from .water_mantaflow import cache_folder, fluid_domain
        settings = fluid_domain(domain)
        if settings is not None:
            cache = cache_folder(settings)
            data = cache / 'data'
            source = {'provider': 'MANTAFLOW', 'cache': str(cache),
                      'matrix': [list(row) for row in domain.matrix_world],
                      'bake_stat': ([data.stat().st_mtime_ns, len(list(data.glob('*.vdb')))]
                                    if data.is_dir() else None)}
    elif getattr(s, 'water_flow_provider', 'FLIP_FLUIDS') in PARTICLE_PROVIDERS and domain is not None:
        from .water_particles import _cache_files
        files = []
        for item in (domain, getattr(s, 'water_flow_surface', None)):
            for cache in (_cache_files(item) if item is not None else ()):
                path = Path(bpy.path.abspath(cache.filepath))
                files.append([str(path), path.stat().st_mtime_ns if path.is_file() else None])
        source = {'provider': s.water_flow_provider, 'files': files,
                  'surface': getattr(getattr(s, 'water_flow_surface', None), 'name', None),
                  'matrix': [list(row) for row in domain.matrix_world]}
    elif domain is not None and hasattr(domain, 'flip_fluid') and domain.flip_fluid.object_type == 'TYPE_DOMAIN':
        cache = Path(domain.flip_fluid.domain.cache.get_cache_abspath())
        stats = cache / 'flipstats.data'
        source = {'cache': str(cache), 'matrix': [list(row) for row in domain.matrix_world],
                  'bake_stat': [stats.stat().st_size, stats.stat().st_mtime_ns] if stats.is_file() else None}
    return {'domain': domain.name if domain else None, 'source': source, 'influence': s.water_flow_influence,
            'hydrodynamic_pressure':getattr(s,'water_hydrodynamic_pressure',False),
            'pressure_strength':getattr(s,'water_pressure_strength',1.),
            'pressure_tangential':getattr(s,'water_pressure_tangential',0.),
            'wet_fabric':[getattr(s,k,0.) for k in WET_FABRIC_KEYS],
            'coupling_strength':getattr(s,'water_flow_coupling_strength',1.),
            'native_flow':getattr(s,'water_flow_native_flow',1.),
            'velocity_scale': s.water_flow_velocity_scale, 'resolution': s.water_flow_resolution,
            'custom_resolution':getattr(s,'water_flow_custom_resolution',100),
            'container': s.water_flow_container}


def water_grid_plan(provider, settings):
    source_voxel=None
    dx=getattr(getattr(getattr(provider,'cache',None),'bounds',None),'dx',None)
    if getattr(provider,'stream_bounds',None):dx=provider.stream_bounds['dx']
    if dx is not None:
        scale=float(np.linalg.norm(provider.cache_matrix[:3,:3],axis=0).min())
        source_voxel=float(dx)*scale
    from ..gaia.flow_budget import coupling_budget
    context=getattr(provider,'context',None)
    scene=getattr(context,'scene',None)
    native=False
    if context is not None:
        from .preferences import _read_registry
        registry,_error=_read_registry()
        native=bool(registry.selected and registry.selected.protocol_version=='0.26')
    # Native export merges identical fields across disjoint Cloth groups.
    identities=set()
    for obj in getattr(scene,'objects',()):
        s=getattr(obj,'cloth_next',None)
        if s and s.enabled and s.role=='CLOTH' and s.water_flow_enabled:
            identities.add((s.water_flow_container,s.water_flow_influence,
                s.water_flow_velocity_scale,s.water_flow_coupling_strength,s.water_flow_native_flow))
    budget=coupling_budget(frames=getattr(provider,'frame_count',1),
                           fields=max(1,len(identities)),native=native)
    return grid_plan(provider.minimum,provider.maximum,settings.water_flow_resolution or 'AUTO',
                     getattr(settings,'water_flow_custom_resolution',100),source_voxel_size=source_voxel,
                     budget=budget)


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
    provider = water_provider(context, obj, frames)
    fps = context.scene.render.fps / context.scene.render.fps_base
    layout = water_grid_plan(provider,s)
    dims = layout['dimensions']
    metadata = {'provider': provider.identity, 'frames': [first,last], 'fps': fps,
                'dimensions': dims, 'algorithm': 'trilinear-splat-support-v2',
                'field_format': 'velocity-occupancy-v1', 'physical_velocity_units': 'm/s',
                'velocity_policy':'unclamped', 'grid_layout':layout}
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
        from ..gaia.particle_stream import ParticleFileSample
        from ..gaia.water_field import reconstruct_chunks
        for frame in frames:
            sample=getattr(provider,'sample_stream',provider.sample)(frame)
            options=dict(max_speed=None,support_threshold=layout['support_threshold'])
            if isinstance(sample,ParticleFileSample):
                field=reconstruct_chunks(sample.chunks(),provider.minimum,provider.maximum,dims,**options)
            else:
                positions,velocities=sample
                field=reconstruct(positions,velocities,provider.minimum,provider.maximum,dims,**options)
            yield frame, (frame-first)/fps, field
            # Particle-file samples stream in chunks and bind no arrays here.
            del field, sample
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
    if any(getattr(obj.cloth_next,'water_hydrodynamic_pressure',False) for obj in targets):
        return apply_hydrodynamic_stream_payload(context,payload,resolved,first,last,targets)
    if str(getattr(resolved,'protocol_version','')) == '0.26':
        return apply_native_water_stream_payload(context, payload, resolved, first, last)
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


def pressure_model(targets):
    """One sheet-drag model for every pressure cloth (settings are scene-shared)."""
    values={(round(float(getattr(o.cloth_next,'water_pressure_strength',1.)),6),
             round(float(getattr(o.cloth_next,'water_pressure_tangential',0.)),6)) for o in targets}
    if len(values)!=1:
        raise ValueError('Hydrodynamic Pressure needs the same Pressure Strength and Tangential Drag on all cloth')
    strength,tangential=next(iter(values))
    if not (np.isfinite(strength) and np.isfinite(tangential)) or strength<0 or tangential<0:
        raise ValueError('Invalid Hydrodynamic Pressure strength or tangential drag')
    # Strength scales the dynamic pressure 1/2 rho |u|^2 through the density.
    return dict(density_kg_m3=1000.*strength,normal_coefficient=1.,tangential_coefficient=tangential,
                wet_area_fraction=1.,velocity_m_s=[0.,0.,0.],volume_mode=1)


WET_FABRIC_KEYS=('water_wet_fabric','water_fiber_density','water_absorption','water_wet_time','water_dry_time','water_wet_cling','water_wet_damping')


def wet_fabric_model(targets, gravity_blender):
    """Extra sheet-model fields for buoyancy and soaking; empty when disabled."""
    values={tuple(round(float(getattr(o.cloth_next,k,0.)),6) for k in WET_FABRIC_KEYS) for o in targets}
    if len(values)!=1:
        raise ValueError('Wet Fabric settings must be the same on all pressure cloth')
    enabled,fiber,absorption,wet_time,dry_time,cling,damping=next(iter(values))
    if not enabled:
        return {}
    if not np.isfinite([fiber,absorption,wet_time,dry_time,cling,damping]).all() or fiber<=0 or min(absorption,wet_time,dry_time,cling,damping)<0:
        raise ValueError('Invalid Wet Fabric settings')
    from ..ppf.coordinates import blender_vector_to_ppf
    gravity=[float(v) for v in blender_vector_to_ppf(gravity_blender)]
    model=dict(gravity_m_s2=gravity,buoyancy_ratio=1000./fiber,absorption=absorption,
               wet_time_s=wet_time,dry_time_s=dry_time,wet_cling=cling)
    # Older wet-fabric workers refuse unknown fields; only send damping when used.
    if damping>0:
        model['wet_damping']=damping
    return model


def _scene_gravity(context):
    from .solver_test import _force_state
    return tuple(_force_state(context)[0].gravity)


def apply_hydrodynamic_stream_payload(context,payload,resolved,first,last,targets):
    from ..ppf.hydrodynamic_capability import require_hydrodynamic_capability
    from ..ppf.models import ConnectionOwnership
    from ..ppf.schema import envelope
    from ..gaia.streaming import WaterStreamReader
    from mathutils import Vector
    require_hydrodynamic_capability(resolved.executable_path)
    if any(getattr(o.cloth_next,'water_wet_fabric',False) for o in targets):
        from ..ppf.hydrodynamic_capability import wet_fabric_capability
        if not wet_fabric_capability(resolved.executable_path):
            raise ValueError('Wet Fabric needs a GAIA build with wet-fabric support; update GAIA or disable Wet Fabric')
        if any(getattr(o.cloth_next,'water_wet_damping',0.)>0 for o in targets):
            from ..ppf.hydrodynamic_capability import wet_damping_capability
            if not wet_damping_capability(resolved.executable_path):
                raise ValueError('Wet Damping needs a newer GAIA build; update GAIA or set Wet Damping to 0')
    if resolved.ownership is not ConnectionOwnership.OWNED_PROCESS:
        raise ValueError('Hydrodynamic Pressure requires an owned isolated solver')
    if not all(getattr(obj.cloth_next,'water_hydrodynamic_pressure',False) for obj in targets):
        raise ValueError('Hydrodynamic Pressure cannot be mixed with Native Flow')
    if context.scene.unit_settings.scale_length!=1.:
        raise ValueError('Hydrodynamic Pressure requires Unit Scale 1')
    raw=payload.read_bytes() if isinstance(payload,Path) else payload
    tree=envelope.loads_envelope(raw,envelope.KIND_PARAM,schema_version=2)
    fps=context.scene.render.fps/context.scene.render.fps_base
    if abs(float(tree['scene']['fps'])-fps)>1e-7:
        raise ValueError('Hydrodynamic Pressure requires physical playback speed 1')
    from .water_preparation import pressure_source_end
    source_end=pressure_source_end(targets,first,last)
    paths=set();fingerprints=set()
    for obj in targets:
        s=obj.cloth_next
        if abs(s.water_flow_influence-1.)>1e-7 or abs(s.water_flow_velocity_scale-1.)>1e-7:
            raise ValueError('Hydrodynamic Pressure requires Influence and Velocity Scale 1 on all cloth')
        path=prepare_container(context,obj,first,source_end)
        with GaiaContainer(path) as container:digest=container.manifest['water']['fingerprint']
        with WaterStreamReader(path,first=int(first),last=source_end,fps=fps,expected_fingerprint=digest):pass
        paths.add(str(Path(path).resolve()));fingerprints.add(digest)
    if len(paths)!=1 or len(fingerprints)!=1:
        raise ValueError('Hydrodynamic Pressure requires one shared FLIP cache for all cloth')
    if tree.get('force_field',{}).get('fields'):
        raise ValueError('Hydrodynamic Pressure cannot run together with force fields')
    def report(current,total):
        from ..bake.controller import shared_controller
        from ..bake.status import BakeActivity
        shared_controller.update(status_message='Measuring cloth extent for Hydrodynamic Pressure',
            activity_code=BakeActivity.CAPTURING_GEOMETRY,
            progress_current=current,progress_total=total)
    bounds=hydrodynamic_bounds(context,targets,first,last,progress=report)
    descriptor=dict(version=1,field_kind='experimental-hydrodynamic',first=int(first),last=int(last),
        source_last=source_end,
        fps=fps,path=next(iter(paths)),fingerprint=next(iter(fingerprints)),bounds=bounds,
        model={**pressure_model(targets),**wet_fabric_model(targets,_scene_gravity(context))})
    return payload,None,json.dumps(descriptor,sort_keys=True,separators=(',',':'),allow_nan=False)


def apply_native_water_stream_payload(context, payload, resolved, first, last):
    """Prepare an owned native sequence for bounded file encoding in the worker."""
    from ..ppf.models import ConnectionOwnership
    from ..ppf.compatibility import protocol_profile
    from ..ppf.adapters import ADAPTERS
    from ..ppf.schema import envelope
    from ..gaia.native_fluid import validate_native_water, MAX_PAYLOAD_BYTES, BRICK
    from ..gaia.streaming import WaterStreamReader
    from ..gaia.streaming import MAX_STREAM_VALUES_BYTES
    profile = protocol_profile(str(resolved.protocol_version), str(getattr(resolved,'schema_version','2')))
    if (profile is None or not ADAPTERS[profile.adapter_id].native_fluid
            or resolved.ownership is not ConnectionOwnership.OWNED_PROCESS):
        raise ValueError('Native Fluid Interaction streaming requires local official GAIA 0.26 / schema 2')
    tree = envelope.loads_envelope(payload.read_bytes() if isinstance(payload,Path) else payload,
                                   envelope.KIND_PARAM,schema_version=2)
    existing = tree.setdefault('force_field', {'fields':[], 'scripts':[]})
    if 'grids' in existing:
        raise ValueError('GAIA 0.26 refuses legacy grids; rebuild the field export')
    fps = context.scene.render.fps/context.scene.render.fps_base
    units = getattr(getattr(context.scene,'unit_settings',None),'scale_length',1.)
    if not np.isfinite(units) or not np.isclose(units,1.):
        raise ValueError('Native Fluid Interaction currently requires Blender Unit Scale 1')
    descriptor = {'version':1,'field_kind':'native-file','first':int(first),'last':int(last),
                  'fps':fps,'solver_fps':float(tree['scene']['fps']),'packing_recipe':1,'targets':[]}
    active_bytes = 0
    for obj in context.scene.objects:
        s = getattr(obj,'cloth_next',None)
        if not s or not s.enabled or s.role!='CLOTH' or not s.water_flow_enabled:
            continue
        if abs(s.water_flow_velocity_scale-1.)>1e-7:
            raise ValueError('Native Fluid Interaction requires Velocity Scale 1; use Coupling Strength to change coupling')
        indices = [i for i,(_,names,_) in enumerate(tree['group']) if obj.name in names]
        if len(indices)!=1 or len(tree['group'][indices[0]][1])!=1:
            raise ValueError('Native Fluid Interaction requires one solver group per cloth object')
        path = prepare_container(context,obj,first,last)
        with GaiaContainer(path) as container:
            digest = container.manifest['water']['fingerprint']
            import json
            array_digest = hashlib.sha256(json.dumps(container.manifest['frames'],
                sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
        strength = getattr(s,'water_flow_coupling_strength',1.)
        flow = getattr(s,'water_flow_native_flow',1.)
        with WaterStreamReader(path,first=int(first),last=int(last),fps=fps,
                               expected_fingerprint=digest) as reader:
            validate_native_water(reader.container.manifest['water'])
            if (not np.isfinite((strength,flow)).all() or strength<0 or not 0<=flow<=1):
                raise ValueError('Invalid native fluid strength or flow')
            # Targets are encoded sequentially; only one dense/packed frame is live.
            from ..gaia.flow_budget import native_encoding_bytes
            active_bytes = max(active_bytes,native_encoding_bytes(reader.dimensions))
        descriptor['targets'].append({'path':str(Path(path).resolve()),'fingerprint':digest,
            'arrays_digest':array_digest,'groups':indices,'influence':s.water_flow_influence,'velocity_scale':1.,
            'strength':strength,'flow':flow})
        tree['group'][indices[0]][0]['force-field-weight']=1.
    if active_bytes>MAX_STREAM_VALUES_BYTES:
        raise ValueError('Native fluid frame encoding exceeds the 128 MiB active memory budget')
    from ..gaia.native_fluid import merge_disjoint_fluid_targets
    descriptor['targets'] = merge_disjoint_fluid_targets(descriptor['targets'])
    data = envelope.dumps_envelope(envelope.KIND_PARAM,tree,schema_version=2)
    if len(data)>MAX_PAYLOAD_BYTES:
        raise ValueError('Base field PARAM exceeds the 32 MiB upload budget')
    import json
    return data,hashlib.sha256(data).hexdigest(),json.dumps(descriptor,sort_keys=True,
                                                        separators=(',',':'),allow_nan=False)


def apply_native_water_payload(context, payload, resolved, first, last):
    """Native velocity feedback; shares the existing PARAM transfer/lifecycle."""
    from ..ppf.compatibility import protocol_profile
    from ..ppf.adapters import ADAPTERS
    from ..ppf.schema import envelope
    from ..gaia.native_fluid import native_fluid_payload, MAX_PAYLOAD_BYTES
    profile = protocol_profile(str(resolved.protocol_version), str(getattr(resolved,'schema_version','2')))
    if profile is None or not ADAPTERS[profile.adapter_id].native_fluid:
        raise ValueError('Native Fluid Interaction requires official GAIA 0.26 / schema 2')
    tree = envelope.loads_envelope(payload.read_bytes() if isinstance(payload,Path) else payload,
                                   envelope.KIND_PARAM,schema_version=2)
    existing = tree.setdefault('force_field', {'fields':[], 'scripts':[]})
    if 'grids' in existing:
        raise ValueError('GAIA 0.26 refuses legacy grids; rebuild the field export')
    fps = context.scene.render.fps/context.scene.render.fps_base
    scene_units = getattr(getattr(context.scene,'unit_settings',None),'scale_length',1.)
    if not np.isfinite(scene_units) or not np.isclose(scene_units,1.):
        raise ValueError('Native Fluid Interaction currently requires Blender Unit Scale 1; other unit scales are not certified')
    for obj in context.scene.objects:
        s = getattr(obj,'cloth_next',None)
        if not s or not s.enabled or s.role!='CLOTH' or not s.water_flow_enabled:
            continue
        if abs(s.water_flow_velocity_scale-1.)>1e-7:
            raise ValueError('Native Fluid Interaction requires Velocity Scale 1; use Coupling Strength to change coupling')
        indices = [i for i,(_,names,_) in enumerate(tree['group']) if obj.name in names]
        if len(indices)!=1 or len(tree['group'][indices[0]][1])!=1:
            raise ValueError('Native Fluid Interaction requires one solver group per cloth object')
        path = prepare_container(context,obj,first,last)
        with GaiaContainer(path) as container:
            field = native_fluid_payload(container,indices,first=int(first),last=int(last),fps=fps,
                solver_fps=float(tree['scene']['fps']),
                strength=getattr(s,'water_flow_coupling_strength',1.)*s.water_flow_influence,
                flow=getattr(s,'water_flow_native_flow',1.))
        existing['fields'].extend(field['fields'])
        tree['group'][indices[0]][0]['force-field-weight']=1.
        # Whole PARAM includes all targets/scripts, not just the most recent volume.
        if len(envelope.dumps_envelope(envelope.KIND_PARAM,tree,schema_version=2))>MAX_PAYLOAD_BYTES:
            raise ValueError('Combined native fluid PARAM exceeds the 32 MiB upload budget')
    data = envelope.dumps_envelope(envelope.KIND_PARAM,tree,schema_version=2)
    return data,hashlib.sha256(data).hexdigest()


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


FLIP_EXPORT_SETTINGS = {
    'fluid_particle_output_amount': 1.,
    'enable_fluid_particle_surface_output': True,
    'enable_fluid_particle_boundary_output': True,
    'enable_fluid_particle_interior_output': True,
    'fluid_particle_source_id_blacklist': -1,
    'enable_fluid_particle_velocity_vector_attribute': True,
    'enable_fluid_particle_output': True,
}


def configure_flip_exports(domain):
    """Configure the public FLIP RNA settings only; never bake or edit files."""
    flip = getattr(domain, 'flip_fluid', None)
    if flip is None or flip.object_type != 'TYPE_DOMAIN':
        raise ValueError('Select a FLIP Fluids Domain first')
    d = flip.domain
    if getattr(getattr(d, 'bake', None), 'is_simulation_running', False):
        raise ValueError('Finish or cancel the FLIP bake before changing export settings')
    particles = getattr(d, 'particles', None)
    missing = [key for key in FLIP_EXPORT_SETTINGS if not hasattr(particles, key)]
    if missing:
        raise ValueError('This FLIP version lacks required particle export settings: '+', '.join(missing))
    before = {key:getattr(particles,key) for key in FLIP_EXPORT_SETTINGS}
    changed = [key for key,value in FLIP_EXPORT_SETTINGS.items() if before[key] != value]
    attempted = []
    try:
        for key in changed:
            attempted.append(key)
            setattr(particles,key,FLIP_EXPORT_SETTINGS[key])
    except Exception as exc:
        rollback_errors = []
        for key in reversed(attempted):
            try: setattr(particles,key,before[key])
            except Exception: rollback_errors.append(key)
        detail = '; could not restore: '+', '.join(rollback_errors) if rollback_errors else ''
        raise ValueError(f'Cannot configure FLIP particle export: {exc}{detail}') from exc
    return len(changed)


class CLOTHNEXT_OT_enable_flip_velocity_export(bpy.types.Operator):
    bl_idname = 'cloth_next.enable_flip_velocity_export'
    bl_label = 'Enable FLIP Velocity Export'
    bl_description = 'Enable all liquid particles and velocity attributes on the selected FLIP Domain; rebake FLIP afterwards'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from ..bake.controller import shared_controller
        obj = getattr(context,'object',None)
        s = getattr(obj,'cloth_next',None)
        domain = getattr(s,'water_flow_domain',None)
        flip = getattr(domain,'flip_fluid',None)
        return bool(s and s.enabled and s.role=='CLOTH' and s.water_flow_enabled
                    and flip and flip.object_type=='TYPE_DOMAIN'
                    and not shared_controller.snapshot().active
                    and not getattr(getattr(flip.domain,'bake',None),'is_simulation_running',False))

    def execute(self, context):
        try:
            changed = configure_flip_exports(context.object.cloth_next.water_flow_domain)
        except Exception as exc:
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}
        self.report({'INFO'},'FLIP particle and velocity export enabled. Rebake FLIP to update its cache.'
                    if changed else 'FLIP export settings are already enabled. Rebake if velocity cache files are missing.')
        return {'FINISHED'}


class CLOTHNEXT_OT_select_water_source(bpy.types.Operator):
    bl_idname = 'cloth_next.select_water_source'
    bl_label = 'Select Fluid Source'
    bl_options = {'INTERNAL'}
    provider: bpy.props.StringProperty(default='FLIP_FLUIDS')

    @classmethod
    def description(cls, _context, properties):
        return next((tooltip for identifier,_name,_enabled,tooltip in WATER_SOURCE_OPTIONS
                     if identifier==properties.provider),'This fluid provider is not supported yet.')

    def execute(self, context):
        supported = {identifier for identifier,_name,enabled,_tip in WATER_SOURCE_OPTIONS if enabled}
        if self.provider not in supported:
            self.report({'ERROR'}, 'This fluid provider is not supported yet.')
            return {'CANCELLED'}
        s = getattr(getattr(context, 'object', None), 'cloth_next', None)
        if s is not None and s.water_flow_provider != self.provider:
            s.water_flow_provider = self.provider
            s.water_flow_domain = None  # a domain of the other kind cannot stay
        return {'FINISHED'}


class CLOTHNEXT_MT_water_source_provider(bpy.types.Menu):
    bl_idname = 'CLOTHNEXT_MT_water_source_provider'
    bl_label = 'Fluid Provider'

    def draw(self, _context):
        for identifier,label,supported,_tooltip in WATER_SOURCE_OPTIONS:
            row=self.layout.row()
            row.enabled=supported
            current=getattr(getattr(_context,'object',None),'cloth_next',None)
            chosen=getattr(current,'water_flow_provider','FLIP_FLUIDS')==identifier
            op=row.operator('cloth_next.select_water_source',text=label,
                            icon='CHECKMARK' if chosen else 'NONE')
            op.provider=identifier


class CLOTHNEXT_OT_prepare_water_flow(bpy.types.Operator):
    bl_idname = 'cloth_next.prepare_water_flow'
    bl_label = 'Prepare .gaia Water Field'
    bl_description = 'Convert the baked FLIP fluid into the .gaia cache the Bake reads. Then start the Bake in the Cloth panel'
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


def _native_registry():
    from . import preferences
    registry, _ = preferences._read_registry()
    return registry, bool(registry.selected and registry.selected.protocol_version == '0.26')


def _gaia_access_granted() -> bool:
    from . import gaia_account
    return gaia_account.access_granted()


class _WaterFlowSubpanel:
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'physics'
    bl_parent_id = 'CLOTHNEXT_PT_water_flow'
    @classmethod
    def poll(cls, context):
        return CLOTHNEXT_PT_water_flow.poll(context)
    def _body(self, context):
        from ..bake.controller import shared_controller
        s, layout = context.object.cloth_next, self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = s.water_flow_enabled and not shared_controller.snapshot().active
        return s, layout


class CLOTHNEXT_PT_water_flow(bpy.types.Panel):
    bl_label = 'GAIA Water Flow'
    bl_idname = 'CLOTHNEXT_PT_water_flow'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'physics'
    bl_options = {'DEFAULT_CLOSED'}
    def draw_header(self, context):
        from ..bake.controller import shared_controller
        from . import icon_registry
        s = context.object.cloth_next
        row = self.layout.row(align=True)
        # Switching on needs GAIA access; switching off always stays possible.
        row.enabled = (not shared_controller.snapshot().active
                       and (s.water_flow_enabled or _gaia_access_granted()))
        row.prop(s, 'water_flow_enabled', text='')
        row.label(text='', **icon_registry.icon_kwargs('gaia_water', 'PHYSICS'))
    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and hasattr(obj, 'cloth_next') and obj.cloth_next.enabled and obj.cloth_next.role == 'CLOTH'
    def draw(self, context):
        from ..bake.controller import shared_controller
        s, layout = context.object.cloth_next, self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        if not s.water_flow_enabled and not _gaia_access_granted():
            row = layout.row()
            row.label(text='Needs GAIA access', icon='LOCKED')
            row.operator('clothnext.open_preferences', text='Preferences')
        layout = layout.column()
        layout.enabled = s.water_flow_enabled and not shared_controller.snapshot().active
        _registry, native = _native_registry()
        # Where the water comes from.
        source = layout.column(align=True)
        provider = source.split(factor=0.4, align=True)
        name = provider.row()
        name.alignment = 'RIGHT'
        name.label(text='Source')
        kind = s.water_flow_provider
        mantaflow = kind == 'MANTAFLOW'
        label = next(name for identifier,name,_ok,_tip in WATER_SOURCE_OPTIONS if identifier==kind)
        provider.menu('CLOTHNEXT_MT_water_source_provider', text=label)
        domain = source.row(align=True)
        if kind in PARTICLE_PROVIDERS:
            domain.prop(s, 'water_flow_domain', text='Particles')
            source.prop(s, 'water_flow_surface', text='Surface')
            if s.water_hydrodynamic_pressure and s.water_flow_surface is None:
                source.label(text='Pick the liquid mesh for Hydrodynamic Pressure', icon='INFO')
        else:
            domain.prop(s, 'water_flow_domain', text='Domain')
        if kind == 'FLIP_FLUIDS':
            domain.operator('cloth_next.enable_flip_velocity_export', text='', icon='EXPORT')
        elif mantaflow:
            hint = mantaflow_hint(s.water_flow_domain)
            if hint:
                source.label(text=hint, icon='INFO')
        # The prepared fluid cache the cloth bake reads.
        box = layout.box()
        ready = bool(s.water_flow_container) and Path(bpy.path.abspath(s.water_flow_container)).is_file()
        box.label(text='Fluid cache ready' if ready else 'Fluid cache not prepared yet',
                  icon='CHECKMARK' if ready else 'INFO')
        row = box.row()
        row.scale_y = 1.3
        row.operator('cloth_next.prepare_water_flow', text='Prepare Fluid Cache', icon='FILE_REFRESH')
        response = layout.column(align=True)
        if not native:
            response.prop(s, 'water_flow_velocity_scale', text='Velocity Scale')
            return
        # Velocity-relaxation Native Flow stays only as the code fallback for GAIA
        # builds without pressure support; its controls are no longer shown.
        if s.water_hydrodynamic_pressure and abs(s.water_flow_influence - 1.) > 1e-7:
            # Hidden under pressure, but a leftover value would block the Bake.
            response.prop(s, 'water_flow_influence', text='Influence')
            response.label(text='Set Influence to 1 for Hydrodynamic Pressure.', icon='ERROR')
        if abs(s.water_flow_velocity_scale - 1.) > 1e-7:
            response.prop(s, 'water_flow_velocity_scale', text='Velocity Scale')
            response.label(text='Set Velocity Scale to 1 for Native Flow.', icon='ERROR')


class CLOTHNEXT_PT_water_pressure(_WaterFlowSubpanel, bpy.types.Panel):
    bl_label = 'Hydrodynamic Pressure'
    bl_idname = 'CLOTHNEXT_PT_water_pressure'
    @classmethod
    def poll(cls, context):
        return super().poll(context) and _native_registry()[1]
    def draw_header(self, context):
        from ..ppf.hydrodynamic_capability import hydrodynamic_capability
        s = context.object.cloth_next
        registry, _native = _native_registry()
        hydro = hydrodynamic_capability(registry.selected.executable if registry.selected else None)
        row = self.layout.row()
        row.enabled = s.water_flow_enabled and (hydro.available or bool(s.water_hydrodynamic_pressure))
        row.prop(s, 'water_hydrodynamic_pressure', text='')
    def draw(self, context):
        s, layout = self._body(context)
        layout.active = bool(s.water_hydrodynamic_pressure)
        layout.prop(s, 'water_pressure_strength', text='Strength')
        layout.prop(s, 'water_pressure_tangential')


class CLOTHNEXT_PT_wet_fabric(_WaterFlowSubpanel, bpy.types.Panel):
    bl_label = 'Wet Fabric Physics'
    bl_idname = 'CLOTHNEXT_PT_wet_fabric'
    bl_parent_id = 'CLOTHNEXT_PT_water_pressure'
    bl_options = {'DEFAULT_CLOSED'}
    @classmethod
    def poll(cls, context):
        return CLOTHNEXT_PT_water_pressure.poll(context)
    def draw_header(self, context):
        s = context.object.cloth_next
        row = self.layout.row()
        row.enabled = s.water_flow_enabled and bool(s.water_hydrodynamic_pressure)
        row.prop(s, 'water_wet_fabric', text='')
    def draw(self, context):
        s, layout = self._body(context)
        layout.active = bool(s.water_hydrodynamic_pressure and s.water_wet_fabric)
        hint = layout.row()
        hint.alignment = 'RIGHT'
        hint.label(text='Weight and cling of soaked cloth')
        column = layout.column(align=True)
        column.prop(s, 'water_fiber_density')
        column.prop(s, 'water_absorption', text='Absorption')
        column = layout.column(align=True)
        column.prop(s, 'water_wet_time')
        column.prop(s, 'water_dry_time')
        column = layout.column(align=True)
        column.prop(s, 'water_wet_cling')
        column.prop(s, 'water_wet_damping')


class CLOTHNEXT_PT_water_cache(_WaterFlowSubpanel, bpy.types.Panel):
    bl_label = 'Fluid Cache Settings'
    bl_idname = 'CLOTHNEXT_PT_water_cache'
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        s, layout = self._body(context)
        layout.prop(s, 'water_flow_resolution')
        if s.water_flow_resolution == 'CUSTOM':
            layout.prop(s, 'water_flow_custom_resolution')
        info = prepared_grid_info(s)
        if info and info.get('budget_adjusted'):
            layout.label(text=f"Grid reduced to {info['effective_resolution']} to fit memory.", icon='INFO')
        layout.prop(s, 'water_flow_container', text='.gaia File')


class CLOTHNEXT_PT_water_flow_display(bpy.types.Panel):
    bl_label = 'Source Flow Preview'
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
            from .physics_ui import _developer_tools_enabled
            info = _diagnostics.get(context.object.name) if _developer_tools_enabled(context) else None
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
        from . import preferences
        registry,_ = preferences._read_registry()
        native = bool(registry.selected and registry.selected.protocol_version == '0.26')
        key = (str(path), path.stat().st_mtime_ns, bpy.context.scene.frame_current,
               s.water_flow_vector_stride, s.water_flow_vector_scale,
               s.water_flow_influence, s.water_flow_velocity_scale,
               s.water_flow_debug_mode, s.water_flow_show_bounds, native)
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
                        mode='MAGNITUDE' if native else s.water_flow_debug_mode,
                        physical_scale=1. if native else s.water_flow_influence*s.water_flow_velocity_scale)
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
    _grid_display_cache.clear()


CLASSES = (CLOTHNEXT_OT_select_water_source, CLOTHNEXT_MT_water_source_provider,
           CLOTHNEXT_OT_enable_flip_velocity_export, CLOTHNEXT_OT_prepare_water_flow,
           CLOTHNEXT_PT_water_flow, CLOTHNEXT_PT_water_pressure, CLOTHNEXT_PT_wet_fabric,
           CLOTHNEXT_PT_water_cache, CLOTHNEXT_PT_water_flow_display)
