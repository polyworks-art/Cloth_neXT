# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned .gaia ZIP container; frame-wise I/O, atomic replace, no extraction."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import uuid
import zipfile
import numpy as np
from .water_field import WaterVelocityField, MAX_FRAME_BYTES

SCHEMA = 1


def _bytes(array):
    stream = io.BytesIO()
    np.save(stream, array, allow_pickle=False)
    return stream.getvalue()


def _array(data):
    stream = io.BytesIO(data)
    version = np.lib.format.read_magic(stream)
    if version == (1, 0):
        shape, order, dtype = np.lib.format.read_array_header_1_0(stream)
    elif version == (2, 0):
        shape, order, dtype = np.lib.format.read_array_header_2_0(stream)
    else:
        raise ValueError('Unsupported GAIA array format')
    size = math.prod(shape)*dtype.itemsize
    if dtype != np.dtype('<f4') or size > MAX_FRAME_BYTES or stream.tell()+size != len(data):
        raise ValueError('Corrupted GAIA array size or dtype')
    return np.frombuffer(data, dtype=dtype, offset=stream.tell()).reshape(shape, order='F' if order else 'C')


def write_water_container(path, metadata, frames, *, check_cancel=None):
    path = Path(path)
    if path.suffix.lower() != '.gaia':
        raise ValueError('GAIA caches must use the .gaia extension')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    manifest = {'kind': 'ClothNeXt.GAIA', 'schema': SCHEMA, 'water': metadata, 'frames': []}
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            previous = None
            for number, time, field in frames:
                if check_cancel is not None: check_cancel()
                if not np.isfinite(time) or (previous is not None and time <= previous):
                    raise ValueError('GAIA frame times must strictly increase')
                previous = time
                record = {'frame': int(number), 'time': float(time), 'min': field.minimum,
                          'max': field.maximum, 'arrays': {}}
                for name, array in (('velocity', field.velocity), ('influence', field.influence)):
                    data = _bytes(array)
                    member = f'water/{len(manifest["frames"]):06d}/{name}.npy'
                    archive.writestr(member, data)
                    record['arrays'][name] = {'member': member, 'sha256': hashlib.sha256(data).hexdigest()}
                manifest['frames'].append(record)
            if not manifest['frames']:
                raise ValueError('GAIA water sequence is empty')
            archive.writestr('manifest.json', json.dumps(manifest, allow_nan=False))
        if check_cancel is not None: check_cancel()
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


class GaiaContainer:
    def __init__(self, path):
        self.archive = zipfile.ZipFile(path)
        try:
            names = self.archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('Duplicate GAIA container members')
            self.manifest = json.loads(self._read('manifest.json', 2*1024*1024))
            if self.manifest.get('kind') != 'ClothNeXt.GAIA' or self.manifest.get('schema') != SCHEMA:
                raise ValueError('Unsupported GAIA container format')
            frames = self.manifest['frames']
            times = [f['time'] for f in frames]
            if not frames or not np.isfinite(times).all() or any(b <= a for a,b in zip(times,times[1:])):
                raise ValueError('Invalid GAIA frame timeline')
        except Exception:
            self.archive.close()
            raise

    def _read(self, name, limit):
        info = self.archive.getinfo(name)
        if info.file_size > limit:
            raise ValueError('GAIA container member exceeds memory limit')
        return self.archive.read(name)

    def frame(self, index):
        record = self.manifest['frames'][index]
        arrays = {}
        for name, entry in record['arrays'].items():
            data = self._read(entry['member'], MAX_FRAME_BYTES+1024)
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Corrupted GAIA water cache checksum')
            arrays[name] = _array(data)
        return WaterVelocityField(tuple(record['min']), tuple(record['max']),
                                  arrays['velocity'], arrays['influence'])

    def close(self): self.archive.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
