# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic, deterministic particle-to-grid water velocity reconstruction."""
from dataclasses import dataclass
import hashlib
import json
import numpy as np

MAX_FRAME_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class WaterVelocityField:
    minimum: tuple
    maximum: tuple
    velocity: np.ndarray  # D,H,W,3; Blender world axes, physical m/s
    influence: np.ndarray  # D,H,W, support of liquid samples

    def __post_init__(self):
        v, m = np.asarray(self.velocity), np.asarray(self.influence)
        lo, hi = np.asarray(self.minimum), np.asarray(self.maximum)
        if (v.ndim != 4 or v.shape[-1] != 3 or min(v.shape[:3]) < 2
                or m.shape != v.shape[:3] or lo.shape != (3,) or hi.shape != (3,)
                or not np.isfinite(lo).all() or not np.isfinite(hi).all()
                or not np.all(hi > lo) or not np.isfinite(v).all()
                or not np.isfinite(m).all() or np.any(m < 0) or np.any(m > 1)):
            raise ValueError('Invalid water velocity grid')
        if v.nbytes + m.nbytes > MAX_FRAME_BYTES:
            raise ValueError('Water grid exceeds the per-frame memory limit; lower resolution')

    @property
    def contribution(self):
        return self.velocity * self.influence[..., None]


def fingerprint(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def grid_dimensions(minimum, maximum, longest=32):
    span = np.asarray(maximum, float) - np.asarray(minimum, float)
    if not np.isfinite(span).all() or np.any(span <= 0) or not 2 <= longest <= 128:
        raise ValueError('Invalid water bounds or resolution')
    return tuple(int(v) for v in np.maximum(2, np.ceil(span/span.max()*(longest-1)).astype(int)+1))


def reconstruct(positions, velocities, minimum, maximum, dimensions,
                *, support_threshold=1.0, max_speed=100.0):
    """Normalized trilinear splats. Empty nodes have exactly zero support.

    No global hole filling or extrapolation. Surface samples therefore only
    influence their adjacent cells; they are not treated as a filled volume.
    """
    p, v = np.asarray(positions, float), np.asarray(velocities, float)
    lo, hi = np.asarray(minimum, float), np.asarray(maximum, float)
    dims = np.asarray(dimensions, int)
    if (p.ndim != 2 or p.shape[1] != 3 or v.shape != p.shape or len(p) == 0
            or not np.isfinite(p).all() or not np.isfinite(v).all()
            or lo.shape != (3,) or hi.shape != (3,) or np.any(hi <= lo)
            or not np.isfinite(lo).all() or not np.isfinite(hi).all()
            or dims.shape != (3,) or np.any(dims < 2)
            or not np.isfinite(support_threshold) or support_threshold <= 0
            or not np.isfinite(max_speed) or max_speed <= 0):
        raise ValueError('No usable particles, invalid velocity data, bounds or reconstruction settings')
    count = int(np.prod(dims))
    if count * 32 > MAX_FRAME_BYTES:
        raise ValueError('Water grid exceeds reconstruction memory limit; lower resolution')
    speed = np.linalg.norm(v, axis=1)
    v = v * np.minimum(1., max_speed / np.maximum(speed, 1e-30))[:, None]
    density, momentum = np.zeros(count), np.zeros((count, 3))
    # Bound temporary particle allocation; accumulation order remains stable.
    usable = 0
    for begin in range(0, len(p), 65536):
        pp, vv = p[begin:begin+65536], v[begin:begin+65536]
        inside = np.all((pp >= lo) & (pp <= hi), axis=1)
        pp, vv = pp[inside], vv[inside]
        usable += len(pp)
        q = (pp-lo)/(hi-lo)*(dims-1)
        base = np.minimum(np.floor(q).astype(int), dims-2)
        frac = q-base
        for z in (0, 1):
            for y in (0, 1):
                for x in (0, 1):
                    corner = np.asarray((x, y, z))
                    w = np.prod(np.where(corner, frac, 1-frac), axis=1)
                    index = base+corner
                    flat = (index[:, 2]*dims[1]+index[:, 1])*dims[0]+index[:, 0]
                    np.add.at(density, flat, w)
                    np.add.at(momentum, flat, vv*w[:, None])
    if not usable:
        raise ValueError('No usable water samples inside the reconstruction bounds')
    velocity = np.divide(momentum, density[:, None], out=np.zeros_like(momentum),
                         where=density[:, None] > 1e-12)
    shape = tuple(dims[::-1])
    influence = np.minimum(1., density/support_threshold)
    return WaterVelocityField(tuple(lo), tuple(hi), velocity.reshape(*shape, 3).astype('<f4'),
                              influence.reshape(shape).astype('<f4'))


def solver_grid(field):
    """Blender (x,y,z) -> solver (x,z,-y), including array indexing."""
    from ..ppf.coordinates import blender_vector_to_ppf
    lo, hi = field.minimum, field.maximum
    minimum = blender_vector_to_ppf((lo[0], hi[1], lo[2]))
    maximum = blender_vector_to_ppf((hi[0], lo[1], hi[2]))
    source = field.contribution.transpose(1, 0, 2, 3)[::-1]
    values = np.empty_like(source)
    values[..., 0], values[..., 1], values[..., 2] = source[..., 0], source[..., 2], -source[..., 1]
    return minimum, maximum, np.ascontiguousarray(values, dtype='<f4')


def debug_samples(field, stride=4, threshold=.01):
    """Occupied grid nodes in Blender world space, never solver indices."""
    if stride < 1 or not np.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('Invalid debug sampling')
    d,h,w = field.influence.shape
    z,y,x = np.mgrid[0:d:stride, 0:h:stride, 0:w:stride]
    indices = np.stack((x,y,z),axis=-1).reshape(-1,3)
    values = field.contribution[z,y,x].reshape(-1,3)
    mask = field.influence[z,y,x].reshape(-1) > threshold
    starts = np.asarray(field.minimum)+(np.asarray(field.maximum)-field.minimum)*indices/(np.asarray((w,h,d))-1)
    return starts[mask], values[mask]


def debug_vectors(field, stride=4, scale=.1, *, mode='MAGNITUDE', physical_scale=1.):
    """Bounded display endpoints; physical field/encoder remain untouched."""
    if (not np.isfinite((scale,physical_scale)).all() or scale < 0 or physical_scale < 0
            or mode not in {'MAGNITUDE','DIRECTION','CONSTANT_X'}):
        raise ValueError('Invalid debug vector scale or mode')
    starts, values = debug_samples(field, stride)
    if mode == 'CONSTANT_X':
        values = np.broadcast_to([1.,0.,0.], values.shape).copy()
    else:
        values = values * physical_scale
    speed = np.linalg.norm(values,axis=1)
    mask = speed > 1e-6
    starts, values, speed = starts[mask], values[mask], speed[mask]
    spacing = (np.asarray(field.maximum)-field.minimum)/(np.asarray(field.velocity.shape[:3][::-1])-1)
    cap = min(1., 2.*float(spacing.min()))
    length = np.minimum(scale if mode in {'DIRECTION','CONSTANT_X'} else speed*scale, cap)
    return starts, starts + values/np.maximum(speed[:,None],1e-30)*np.asarray(length)[...,None]
