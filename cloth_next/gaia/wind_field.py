# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic local air velocity shared by preview and solver export."""
from dataclasses import dataclass
import hashlib
import math
import zlib
import numpy as np

MAX_SCHEDULE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class WindSource:
    center: tuple
    direction: tuple
    speed: float
    radius: float
    falloff: float
    turbulence: float
    noise_size: float
    noise_time: float
    seed: int

    def __post_init__(self):
        values = (*self.center, *self.direction, self.speed, self.radius,
                  self.falloff, self.turbulence, self.noise_size, self.noise_time)
        if (len(self.center) != 3 or len(self.direction) != 3
                or not np.isfinite(values).all() or self.speed < 0
                or self.radius <= 0 or self.falloff < 1 or self.turbulence < 0
                or self.noise_size <= 0 or self.noise_time <= 0
                or np.linalg.norm(self.direction) < 1e-12):
            raise ValueError('Invalid local Wind source')


def velocity(points, sources, seconds):
    """Sum smooth spherical sources and bounded swirling Fourier modes."""
    points = np.asarray(points, dtype=np.float64)
    if points.ndim < 2 or points.shape[-1] != 3 or not np.isfinite(points).all() or not math.isfinite(seconds):
        raise ValueError('Wind points and time must be finite')
    result = np.zeros_like(points)
    for source in sources:
        relative = points - source.center
        distance = np.linalg.norm(relative, axis=-1) / source.radius
        weight = np.maximum(0., 1. - distance * distance) ** source.falloff
        direction = np.asarray(source.direction)
        flow = np.broadcast_to(direction / np.linalg.norm(direction) * source.speed, points.shape).copy()
        if source.turbulence:
            # Each mode is perpendicular to its wave vector: a curl mode.
            # The sum's magnitude is bounded by the authored turbulence speed.
            for index in range(8):
                digest = hashlib.sha256(f'{source.seed}:{index}'.encode()).digest()
                numbers = np.frombuffer(digest[:24], dtype='<u4').astype(float) / (2**32 - 1) * 2 - 1
                wave = numbers[:3]; wave /= np.linalg.norm(wave)
                axis = np.cross(wave, numbers[3:6]); axis /= max(np.linalg.norm(axis), 1e-12)
                phase = np.einsum('...i,i->...', relative, wave) * (2 * math.pi / source.noise_size)
                phase += seconds * (2 * math.pi / source.noise_time) + digest[24] / 255 * 2 * math.pi
                flow += source.turbulence / 8 * np.cos(phase)[..., None] * axis
        result += flow * weight[..., None]
    return result


def sample_points(minimum, maximum, dimensions):
    x, y, z = [np.linspace(a, b, int(n)) for a, b, n in zip(minimum, maximum, dimensions)]
    zz, yy, xx = np.meshgrid(z, y, x, indexing='ij')
    return np.stack((xx, yy, zz), axis=-1)


def encode_grid(values, minimum, maximum, times):
    """Blender Z-up nodes -> official GAIA Y-up inclusive corner layout."""
    values = np.asarray(values, dtype='<f4')
    if values.ndim != 5 or values.shape[-1] != 3 or min(values.shape[1:4]) < 2 or not np.isfinite(values).all():
        raise ValueError('Invalid Wind schedule grid')
    if values.nbytes > MAX_SCHEDULE_BYTES:
        raise ValueError('Local Wind schedule exceeds 32 MiB; shorten the Bake range or reduce field resolution')
    if len(times) != len(values) or not np.isfinite(times).all() or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('Wind sample times must increase')
    lo, hi = np.asarray(minimum), np.asarray(maximum)
    if lo.shape != (3,) or hi.shape != (3,) or not np.isfinite((lo, hi)).all() or np.any(hi <= lo):
        raise ValueError('Invalid Wind bounds')
    # Input [T,Z,Y,X,3]; output [T,-Y,Z,X,3] with vectors (x,z,-y).
    converted = values.transpose(0, 2, 1, 3, 4)[:, ::-1, :, :, :][..., [0, 2, 1]].copy()
    converted[..., 2] *= -1
    return {'shape': list(converted.shape), 'min': [float(lo[0]), float(lo[2]), float(-hi[1])],
            'max': [float(hi[0]), float(hi[2]), float(-lo[1])], 'times': list(times),
            'kind': 'air-velocity', 'groups': None, 'data': zlib.compress(converted.tobytes())}


def arrow_lines(points, vectors, scale, maximum_length):
    """Bounded float32 world-space arrows; display scale never changes physics."""
    points = np.asarray(points, float).reshape(-1, 3)
    vectors = np.asarray(vectors, float).reshape(-1, 3)
    if points.shape != vectors.shape or not np.isfinite((points, vectors)).all() or scale <= 0 or maximum_length <= 0:
        raise ValueError('Invalid Wind preview vectors')
    speed = np.linalg.norm(vectors, axis=1)
    mask = speed > 1e-6
    points, vectors, speed = points[mask], vectors[mask], speed[mask]
    if not len(points):
        return np.empty((0, 3), dtype=np.float32)
    direction = vectors / speed[:, None]
    length = np.minimum(speed * scale, maximum_length)
    tip = points + direction * length[:, None]
    reference = np.tile((0., 0., 1.), (len(points), 1))
    reference[np.abs(direction[:, 2]) > .9] = (0., 1., 0.)
    side = np.cross(direction, reference); side /= np.linalg.norm(side, axis=1)[:, None]
    base = tip - direction * length[:, None] * .25
    wing = side * length[:, None] * .12
    return np.ascontiguousarray(np.stack((points, tip, tip, base + wing, tip, base - wing), axis=1).reshape(-1, 3), dtype=np.float32)
