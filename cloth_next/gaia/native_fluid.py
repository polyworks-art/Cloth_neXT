# SPDX-License-Identifier: GPL-3.0-or-later
"""GAIA 0.26 public FluidVolume wire adapter. No host cloth-velocity model.

The authoring cache stores inclusive world-space nodes. Native volume values
are resampled at cell centers within the same bounds, one brick at a time.
Domain axes preserve Blender's indexing; vector components stay domain-local.
Occupancy is always sent separately as normalized density, including still water.
"""
import math
import time
from dataclasses import dataclass
import numpy as np

BRICK = 8
EMPTY = 0xffffffff
CHUNK_BYTES = 1 << 20
MAX_PAYLOAD_BYTES = 32 << 20  # CNX working budget, below upstream's 4 GiB limit
AXES = [[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]]


def chunks(array):
    if not array.size:
        return []
    data = memoryview(np.ascontiguousarray(array)).cast('B')
    return [bytes(data[i:i+CHUNK_BYTES]) for i in range(0, len(data), CHUNK_BYTES)]


def _center_samples(field, sl):
    """Trilinear source-data sampling, bounded to one destination brick."""
    coords=[(np.arange(s.start,s.stop)+.5)*(n-1)/n for s,n in zip(sl,field.influence.shape)]
    base=[np.floor(c).astype(int) for c in coords]
    frac=[c-b for c,b in zip(coords,base)]
    shape=tuple(len(c) for c in coords)
    rho=np.zeros(shape,dtype=np.float64)
    velocity=np.zeros(shape+(3,),dtype=np.float64)
    for z in (0,1):
        for y in (0,1):
            for x in (0,1):
                indices=(base[0][:,None,None]+z,base[1][None,:,None]+y,base[2][None,None,:]+x)
                w=((frac[0] if z else 1-frac[0])[:,None,None]
                   *(frac[1] if y else 1-frac[1])[None,:,None]
                   *(frac[2] if x else 1-frac[2])[None,None,:])
                rho+=field.influence[indices]*w
                velocity+=field.velocity[indices]*w[...,None]
    return velocity,rho


def pack_frame(field, *, rate=1., budget=MAX_PAYLOAD_BYTES, cell_centers=False):
    """Pack D,H,W cache nodes into native x-fastest 8³ bricks, frame-wise."""
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError('Invalid fluid time rate')
    d, h, w = field.influence.shape
    if max(d, h, w) > 32768:
        raise ValueError('Fluid resolution exceeds the official solver limit')
    nx, ny, nz = ((n+BRICK-1)//BRICK for n in (w,h,d))
    table_bytes = nx*ny*nz*4
    if table_bytes > budget:
        raise ValueError('Native fluid brick table exceeds the upload budget')
    table = np.full(nx*ny*nz, EMPTY, dtype='<u4')
    kept = []
    for z in range(nz):
        for y in range(ny):
            for x in range(nx):
                sl = (slice(z*8,min((z+1)*8,d)), slice(y*8,min((y+1)*8,h)),
                      slice(x*8,min((x+1)*8,w)))
                if cell_centers:
                    velocity,rho = _center_samples(field,sl)
                else:
                    velocity,rho = field.velocity[sl],field.influence[sl]
                if not np.any(rho):
                    continue
                if table_bytes+(len(kept)+1)*512*16 > budget:
                    raise ValueError('Native fluid packing exceeds its memory budget; lower the field resolution')
                block = np.zeros((8,8,8,4), dtype='<f4')
                a,b,c = rho.shape
                with np.errstate(over='ignore',invalid='ignore'):
                    # Empty cells must not carry a velocity into neighboring water.
                    block[:a,:b,:c,:3] = np.where(rho[...,None]>0,velocity*rate,0)
                block[:a,:b,:c,3] = rho
                if not np.isfinite(block).all():
                    raise ValueError('Fluid velocity is not finite in solver float32 units')
                table[x+nx*(y+ny*z)] = len(kept)
                kept.append(block.reshape(512,4))
    bricks = np.stack(kept) if kept else np.empty((0,512,4),dtype='<f4')
    return table, bricks


def validate_native_water(water):
    """Shared source contract for complete payloads and held windows."""
    provider = water.get('provider',{})
    if provider.get('mode') != 'FLUID_PARTICLES':
        raise ValueError('Native Fluid Interaction requires baked liquid particle velocities; surface-only velocity is insufficient')
    if 'cache' in provider:
        scale = provider.get('baked_world_scale')
        if scale is None or not math.isfinite(float(scale)) or not math.isclose(float(scale),1.,abs_tol=1e-7):
            raise ValueError('Native Fluid Interaction requires verified FLIP baked World Scale 1; rebuild with valid scale metadata')
    if water.get('physical_velocity_units') != 'm/s':
        raise ValueError('Native Fluid Interaction requires physical m/s velocity units')
    if water.get('velocity_policy') != 'unclamped':
        raise ValueError('Rebuild the fluid cache: legacy speed clipping cannot supply native Fluid Interaction')


@dataclass(frozen=True)
class NativeFluidWindow:
    source_frames: tuple
    times: tuple
    volume: dict
    strength: float
    flow: float
    packed_bytes: int


class NativeFluidStreamReader:
    """One dense frame and a held interval with one lookahead frame resident.

    The public held API permits the state to overshoot the requested frame by
    one timestep. The third sample preserves interpolation during that step;
    the source's final sample holds exactly as in a complete FluidVolume.
    """
    def __init__(self, path, *, first, last, fps, expected_fingerprint,
                 solver_fps=None, strength=1., flow=1.):
        from .streaming import WaterStreamReader, MAX_STREAM_VALUES_BYTES
        self.reader = WaterStreamReader(path, first=first, last=last, fps=fps,
                                        expected_fingerprint=expected_fingerprint)
        try:
            validate_native_water(self.reader.container.manifest['water'])
            self.solver_fps = fps if solver_fps is None else float(solver_fps)
            self.strength, self.flow = float(strength), float(flow)
            if (not np.isfinite((self.solver_fps,self.strength,self.flow)).all()
                    or self.solver_fps<=0 or self.strength<0 or not 0<=self.flow<=1):
                raise ValueError('Invalid native fluid streaming settings')
            self.fingerprint = self.reader.fingerprint
            # Conservative live-array bound includes density, packed window,
            # packing temporaries and the public FluidVolume's immutable copy.
            dense = math.prod(self.reader.dimensions)*16
            slots = math.prod((n+BRICK-1)//BRICK for n in self.reader.dimensions)
            packed_bound = min(MAX_PAYLOAD_BYTES,
                               min(3,last-first+1)*slots*(4+512*16))
            self.active_bytes_bound = dense + 3*packed_bound
            if self.active_bytes_bound > MAX_STREAM_VALUES_BYTES:
                raise ValueError('Native fluid window exceeds the 128 MiB active memory budget')
        except BaseException:
            self.close()
            raise

    def window(self, boundary, *, influence=1., velocity_scale=1.):
        from .streaming import source_pair
        if not math.isfinite(influence) or not 0<=influence<=1 or velocity_scale!=1.:
            raise ValueError('Native Fluid Interaction requires Influence 0–1 and Velocity Scale 1')
        source_pair(boundary, self.reader.first, self.reader.last, self.reader.fps)
        current=self.reader.first+boundary
        frames=tuple(range(current,min(self.reader.last,current+2)+1))
        times = tuple((frame-self.reader.first)/self.solver_fps for frame in frames)
        volume = {'resolution':[], 'tables':[], 'bricks':[], 'cell':[],
                  'corner':[], 'axes':[], 'start':times[0], 'step':1./self.solver_fps}
        timings = {'read_seconds':0., 'decode_validate_seconds':0., 'encode_seconds':0.}
        packed_bytes = 0
        for frame in frames:
            field = self.reader.container.frame(self.reader.index[frame])
            for name, value in self.reader.container.last_frame_timings.items():
                timings[name] += value
            started = time.perf_counter()
            if field.velocity.shape != tuple(self.reader.dimensions[::-1])+(3,):
                raise ValueError(f'Fluid frame {frame} has incompatible dimensions')
            table, bricks = pack_frame(field, rate=self.solver_fps/self.reader.fps,
                                       budget=MAX_PAYLOAD_BYTES-packed_bytes, cell_centers=True)
            packed_bytes += table.nbytes+bricks.nbytes
            spacing = (np.asarray(field.maximum)-field.minimum)/np.asarray(self.reader.dimensions)
            corner = field.minimum
            volume['resolution'].append(list(self.reader.dimensions))
            volume['cell'].append(spacing.tolist())
            volume['corner'].append([float(corner[0]),float(corner[2]),float(-corner[1])])
            volume['axes'].append(AXES)
            volume['tables'].append(table)
            volume['bricks'].append(bricks)
            timings['encode_seconds'] += time.perf_counter()-started
            del field,table,bricks
        self.last_window_timings = timings
        self.last_packed_bytes = packed_bytes
        return NativeFluidWindow(frames,times,volume,self.strength*influence,self.flow,packed_bytes)

    def close(self): self.reader.close()
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def native_fluid_payload(container, groups, *, first, last, fps, strength=1.,
                         flow=1., solver_fps=None, budget=MAX_PAYLOAD_BYTES):
    """Official fields/scripts PARAM contract; refuse missing data and retiming.

    Source m/s is mapped to the simulation clock, never scaled by strength.
    Existing particle reconstruction caches are reused without changing files.
    Surface-only caches are explicitly refused for volumetric liquid coupling.
    """
    from .streaming import WaterStreamReader
    solver_fps = fps if solver_fps is None else solver_fps
    if (not groups or any(type(g) is not int or g<0 for g in groups)
            or not np.isfinite((strength,flow,solver_fps,fps)).all()
            or strength<0 or not 0<=flow<=1 or solver_fps<=0 or fps<=0):
        raise ValueError('Invalid native fluid target, strength, flow or time scale')
    water = container.manifest['water']
    validate_native_water(water)
    # Reuse the central sequence/fingerprint/FPS/shape validator.
    with WaterStreamReader(container.archive.filename,first=first,last=last,fps=fps,
                           expected_fingerprint=water['fingerprint']) as reader:
        volume = {'resolution':[], 'tables':[], 'bricks':[], 'cell':[],
                  'corner':[], 'axes':[], 'start':0., 'step':1./solver_fps}
        total = 0
        for frame in range(first,last+1):
            field = container.frame(reader.index[frame])
            if field.velocity.shape != tuple(reader.dimensions[::-1])+(3,):
                raise ValueError(f'Fluid frame {frame} has incompatible dimensions')
            table,bricks = pack_frame(field,rate=solver_fps/fps,budget=budget-total,cell_centers=True)
            total += table.nbytes+bricks.nbytes
            spacing = (np.asarray(field.maximum)-field.minimum)/np.asarray(reader.dimensions)
            corner = np.asarray(field.minimum)
            volume['resolution'].append(list(reader.dimensions))
            volume['cell'].append(spacing.tolist())
            volume['corner'].append([float(corner[0]),float(corner[2]),float(-corner[1])])
            volume['axes'].append(AXES)
            volume['tables'].append(chunks(table))
            volume['bricks'].append(chunks(bricks))
            del field,table,bricks
    return {'fields':[{'type':'fluid', 'groups':list(groups), 'strength':float(strength),
                       'flow':float(flow), 'use_density':True, 'fluid':volume}], 'scripts':[]}


MAX_FILE_PAYLOAD_BYTES = 1 << 30


def merge_disjoint_fluid_targets(targets):
    """Share an identical volume across disjoint groups; preserve additive fields."""
    import json
    merged = []
    keys = []
    for target in targets:
        item = dict(target, groups=list(target['groups']))
        key = json.dumps({k:v for k,v in item.items() if k != 'groups'},
                         sort_keys=True, separators=(',', ':'), allow_nan=False)
        groups = set(item['groups'])
        found = next((i for i, existing in enumerate(merged)
                      if keys[i] == key and groups.isdisjoint(existing['groups'])
                      and len(groups) == len(item['groups'])), None)
        if found is None:
            merged.append(item)
            keys.append(key)
        else:
            merged[found]['groups'].extend(item['groups'])
    return merged


def native_fluid_payload_file(base_payload, descriptor, path, *, check_cancel=lambda: None,
                              progress=lambda frame: None):
    """Encode the existing full FluidVolume wire contract with one packed frame live."""
    import json
    import uuid
    from pathlib import Path
    from ..ppf.schema import envelope
    from ..ppf.schema.cbor_codec import FileBytes
    from ..core.safe_delete import delete_owned
    from .streaming import WaterStreamReader
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    spool = path.parent / ('native-fluid-spool-' + uuid.uuid4().hex)
    spool.mkdir()
    started = time.monotonic()
    tree = envelope.loads_envelope(base_payload.read_bytes() if isinstance(base_payload,Path) else base_payload,
                                   envelope.KIND_PARAM, schema_version=2)
    fields = tree.setdefault('force_field', {'fields': [], 'scripts': []})['fields']
    packed_total = peak = count = 0
    try:
        with (spool/'frames.bin').open('wb') as output:
            for target in descriptor['targets']:
                with WaterStreamReader(target['path'], first=descriptor['first'],
                        last=descriptor['last'], fps=descriptor['fps'],
                        expected_fingerprint=target['fingerprint']) as reader:
                    validate_native_water(reader.container.manifest['water'])
                    if 'arrays_digest' in target:
                        import hashlib
                        actual = hashlib.sha256(json.dumps(reader.records,sort_keys=True,
                            separators=(',',':'),allow_nan=False).encode()).hexdigest()
                        if actual != target['arrays_digest']:
                            raise ValueError('Prepared native fluid manifest changed after export')
                    volume = {'resolution': [], 'tables': [], 'bricks': [], 'cell': [],
                              'corner': [], 'axes': [], 'start': 0.,
                              'step': 1./descriptor['solver_fps']}
                    for frame in range(reader.first, reader.last+1):
                        check_cancel()
                        field = reader.container.frame(reader.index[frame])
                        if field.velocity.shape != tuple(reader.dimensions[::-1])+(3,):
                            raise ValueError(f'Fluid frame {frame} has incompatible dimensions')
                        table, bricks = pack_frame(field,
                            rate=descriptor['solver_fps']/descriptor['fps'],
                            budget=MAX_PAYLOAD_BYTES, cell_centers=True)
                        size = table.nbytes+bricks.nbytes
                        peak = max(peak, size)
                        packed_total += size
                        if packed_total > MAX_FILE_PAYLOAD_BYTES:
                            raise ValueError('Native fluid file exceeds the 1 GiB transport budget')
                        for key, array in (('tables', table), ('bricks', bricks)):
                            references = []
                            data = memoryview(array).cast('B') if array.size else memoryview(b'')
                            for offset in range(0, len(data), CHUNK_BYTES):
                                check_cancel()
                                block = data[offset:offset+CHUNK_BYTES]
                                references.append(FileBytes(spool/'frames.bin', output.tell(), len(block)))
                                output.write(block)
                            volume[key].append(references)
                        spacing = (np.asarray(field.maximum)-field.minimum)/np.asarray(reader.dimensions)
                        corner = field.minimum
                        volume['resolution'].append(list(reader.dimensions))
                        volume['cell'].append(spacing.tolist())
                        volume['corner'].append([float(corner[0]), float(corner[2]), float(-corner[1])])
                        volume['axes'].append(AXES)
                        del data, table, bricks, field
                        count += 1
                        progress(frame)
                    fields.append({'type': 'fluid', 'groups': target['groups'],
                        'strength': target['strength']*target['influence'],
                        'flow': target['flow'], 'use_density': True, 'fluid': volume})
        digest = envelope.dump_envelope_file(envelope.KIND_PARAM, tree, path,
            schema_version=2, max_bytes=MAX_FILE_PAYLOAD_BYTES,
            progress=lambda *args: check_cancel())
        metrics = {'frame_count': count, 'max_packed_frame_bytes': peak,
                   'packed_bytes': packed_total, 'param_bytes': path.stat().st_size,
                   'encoding_seconds': time.monotonic()-started,
                   'packing_budget_bytes': MAX_PAYLOAD_BYTES,
                   'file_budget_bytes': MAX_FILE_PAYLOAD_BYTES}
        path.with_suffix('.metrics.json').write_text(json.dumps(metrics, indent=2))
        return digest
    except BaseException:
        delete_owned(path,root=path.parent,ownership_authenticated=True,
                     lifecycle_stage='NATIVE_FLUID_ENCODE',artifact_type='native_fluid_partial')
        raise
    finally:
        delete_owned(spool, root=path.parent, ownership_authenticated=True,
                     recursive=True, lifecycle_stage='NATIVE_FLUID_ENCODE',
                     artifact_type='native_fluid_spool')
