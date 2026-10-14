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
            import math
            dense = math.prod(reader.dimensions)*16
            packed = min(MAX_PAYLOAD_BYTES,
                math.prod((n+BRICK-1)//BRICK for n in reader.dimensions)*(4+512*16))
            active_bytes = max(active_bytes,2*dense+3*packed+(1<<20))
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
        layout.prop(s, 'water_flow_enabled', text='Water Flow (Scene)')
        body = layout.column(align=True)
        body.enabled = s.water_flow_enabled
        from . import preferences
        registry, _ = preferences._read_registry()
        native = bool(registry.selected and registry.selected.protocol_version == '0.26')
        body.label(text='Native Flow · GAIA 0.26 (Experimental)' if native else 'Water Flow · GAIA',
                   **icon_registry.icon_kwargs('gaia_water', 'PHYSICS'))
        body.label(text='Shared by all Cloth objects in this scene.', icon='LINKED')
        source = body.box().column(align=True)
        source.label(text='Fluid Source', icon='MOD_FLUIDSIM')
        source.prop(s, 'water_flow_domain')
        source.operator('cloth_next.enable_flip_velocity_export',
                        text='Enable Velocity Export', icon='CHECKMARK')
        source.label(text='Bake FLIP with particle velocities enabled.')
        body.separator()
        response = body.box().column(align=True)
        response.label(text='Cloth Response', icon='MOD_CLOTH')
        response.prop(s, 'water_flow_influence', text='Influence (This Object)')
        if native:
            response.prop(s, 'water_flow_coupling_strength', text='Coupling Strength (1/s)')
            response.prop(s, 'water_flow_native_flow', text='Velocity Damping')
            response.label(text='One-way coupling: fluid moves cloth.')
            if abs(s.water_flow_velocity_scale-1.)>1e-7:
                response.prop(s, 'water_flow_velocity_scale', text='Velocity Scale (This Object)')
                response.label(text='Set Velocity Scale to 1 for Native Flow.', icon='ERROR')
        else:
            response.prop(s, 'water_flow_velocity_scale', text='Velocity Scale (This Object)')
        body.separator()
        cache = body.box().column(align=True)
        cache.label(text='Fluid Cache', icon='FILE_CACHE')
        cache.prop(s, 'water_flow_resolution')
        if s.water_flow_resolution == 'CUSTOM':
            cache.prop(s, 'water_flow_custom_resolution')
        cache.prop(s, 'water_flow_container', text='.gaia File')
        if not s.water_flow_container:
            cache.label(text='Saved in the Cloth cache folder.')
        cache.separator()
        cache.operator('cloth_next.prepare_water_flow', text='Prepare Fluid Cache',
                      **icon_registry.icon_kwargs('gaia_water', 'PHYSICS'))
        body.label(text='Ready? Start Bake in the Cloth panel.', icon='INFO')


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


CLASSES = (CLOTHNEXT_OT_enable_flip_velocity_export, CLOTHNEXT_OT_prepare_water_flow,
           CLOTHNEXT_PT_water_flow, CLOTHNEXT_PT_water_flow_display)
