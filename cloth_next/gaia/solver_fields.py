# SPDX-License-Identifier: GPL-3.0-or-later
"""Central official GAIA 0.23 field adapter, independent of FLIP."""
import zlib
import numpy as np
from .water_field import solver_grid

MAX_UPLOAD_BYTES = 32 * 1024 * 1024


def official_grid_payload(container, groups, *, influence=1., velocity_scale=1.):
    """Encode the *existing official* opaque force_field payload.

    Conversion is frame-wise; preauthored upload has an explicit memory gate.
    Long sequences need the separate two-frame held adapter, not unbounded RAM.
    """
    if not groups or not np.isfinite((influence, velocity_scale)).all() or not 0 <= influence <= 1 or not 0 <= velocity_scale <= 10:
        raise ValueError('Invalid Water Flow target or influence/velocity scale')
    frames = container.manifest['frames']
    first = container.frame(0)
    lo, hi, initial = solver_grid(first)
    shape = (len(frames),) + initial.shape
    if int(np.prod(shape))*4 > MAX_UPLOAD_BYTES:
        raise ValueError('Water Flow schedule exceeds 32 MiB upload budget; lower field resolution or shorten range')
    data = np.empty(shape, dtype='<f4')
    for index in range(len(frames)):
        a, b, values = solver_grid(container.frame(index))
        if a != lo or b != hi or values.shape != initial.shape:
            raise ValueError('Water Flow bounds/resolution must remain constant across the sequence')
        data[index] = values * influence * velocity_scale
    return {'grids': [{'shape': list(shape), 'min': lo, 'max': hi,
                      'times': [f['time'] for f in frames], 'kind': 'air-velocity',
                      'groups': list(groups), 'data': zlib.compress(data.tobytes())}], 'scripts': []}


def update_held_water(session, scene_field, container, index, groups):
    """Public frontend adapter; only two adjacent frames resident at a time."""
    records = container.manifest['frames']
    end = min(index+1, len(records)-1)
    lo, hi, a = solver_grid(container.frame(index))
    other_lo, other_hi, b = solver_grid(container.frame(end))
    if (lo, hi) != (other_lo, other_hi): raise ValueError('Changing water field bounds')
    values = a if end == index else np.stack((a, b))
    times = [records[index]['time']] if end == index else [records[index]['time'], records[end]['time']]
    scene_field.clear().grid(values, lo, hi, times=times, kind='air-velocity', groups=groups)
    session.update_force_field(scene_field)
