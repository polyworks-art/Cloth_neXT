# SPDX-License-Identifier: GPL-3.0-or-later
"""Read complete FLIP vector caches with bounded buffers and explicit ownership."""
from dataclasses import dataclass
from pathlib import Path
import struct
import numpy as np

PARTICLE_CHUNK_SIZE = 65536


def vector_layout(path):
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(16)
    if len(header) != 16:
        raise ValueError(f'Truncated FLIP particle header: {path.name}')
    counts = struct.unpack('<4I', header)
    count, limit = sum(counts[:3]), counts[3]
    offset = 16 + limit * 12
    if not limit or path.stat().st_size != offset + count * 12:
        raise ValueError(f'Invalid FLIP vector cache length: {path.name}')
    return counts, offset


@dataclass(frozen=True)
class ParticleFileSample:
    positions: Path
    velocities: Path
    matrix: tuple
    offset: tuple

    def chunks(self, check_cancel=lambda: None):
        p_layout, p_offset = vector_layout(self.positions)
        v_layout, v_offset = vector_layout(self.velocities)
        if p_layout != v_layout:
            raise ValueError('FLIP particle and velocity layouts differ')
        count = sum(p_layout[:3])
        if not count:
            raise ValueError('No usable fluid samples')
        matrix = np.asarray(self.matrix, dtype=float)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError('Invalid FLIP cache transform')
        with Path(self.positions).open('rb') as p_file, Path(self.velocities).open('rb') as v_file:
            p_file.seek(p_offset); v_file.seek(v_offset)
            for begin in range(0, count, PARTICLE_CHUNK_SIZE):
                check_cancel()
                size = min(PARTICLE_CHUNK_SIZE, count - begin) * 12
                p_raw, v_raw = p_file.read(size), v_file.read(size)
                if len(p_raw) != size or len(v_raw) != size:
                    raise ValueError('FLIP particle cache changed or was truncated while reading')
                p = np.frombuffer(p_raw, dtype='<f4').reshape(-1, 3)
                v = np.frombuffer(v_raw, dtype='<f4').reshape(-1, 3)
                # Match the loader's float32 mesh translation before world transform.
                local = p - np.asarray(self.offset, dtype=np.float32)
                yield local.astype(float) @ matrix[:3, :3].T + matrix[:3, 3], v.astype(float)
