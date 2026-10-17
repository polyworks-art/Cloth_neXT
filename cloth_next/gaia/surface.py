# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict, read-only FLIP BOBJ decoding. Disconnected components are allowed."""
import hashlib
import struct
import numpy as np


def read_surface(path):
    data=path.read_bytes()
    if len(data)<8:raise ValueError(f'{path}: truncated BOBJ header')
    n=struct.unpack_from('<i',data)[0]
    if n<3 or 4+12*n+4>len(data):
        raise ValueError(f'{path}: invalid or truncated BOBJ vertex array')
    x=np.frombuffer(data,'<f4',3*n,4).reshape(-1,3)
    m=struct.unpack_from('<i',data,4+12*n)[0]
    if m<1 or len(data)!=8+12*n+12*m:
        raise ValueError(f'{path}: invalid FLIP BOBJ triangle length')
    f=np.frombuffer(data,'<i4',3*m,8+12*n).reshape(-1,3)
    if not np.isfinite(x).all() or f.min()<0 or f.max()>=n:
        raise ValueError(f'{path}: nonfinite coordinates or invalid triangle indices')
    return x,f,hashlib.sha256(data).hexdigest()

