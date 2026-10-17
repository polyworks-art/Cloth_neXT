"""Probe installed FLIP's read-only API and zero-dt semantics on synthetic data.

Never loads NEON particles into a simulation or advances the original FLIP cache.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--addon', type=Path, required=True)
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.addon, a.cache, a.output = a.addon.resolve(), a.cache.resolve(), a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=True)
    import pefile
    dll = a.addon/'ffengine/lib/libffengine.dll'
    pe = pefile.PE(str(dll))
    exports = sorted(s.name.decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols if s.name)
    (a.output/'engine-exports.json').write_text(json.dumps(exports, indent=2))
    database = a.cache/'export/export_data.sqlite3'
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as c:
        geometry = dict(objects=c.execute('select * from object').fetchall(),
            static_mesh_bytes=c.execute('select object_id,length(mesh_static_data) from mesh_static').fetchall(),
            animated_mesh_ranges=c.execute('select object_id,min(frame_id),max(frame_id),count(*) from mesh_animated group by object_id').fetchall(),
            keyframed_mesh_ranges=c.execute('select object_id,min(frame_id),max(frame_id),count(*) from mesh_keyframed group by object_id').fetchall())
    # Import only the standalone ctypes engine package, not Blender or the add-on.
    sys.path.insert(0, str(a.addon))
    dll_directory = os.add_dll_directory(str(dll.parent))
    from ffengine import FluidSimulation
    sim = FluidSimulation(12, 12, 12, .1)
    result = dict(engine_dll=str(dll), engine_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(),
                  engine_version=sim.get_version(), exports=len(exports), geometry_cache=geometry,
                  sdf_export_symbols=[s for s in exports if any(k in s.lower() for k in ('sdf','levelset'))],
                  source_cache_mutated=False, original_particles_simulated=False)
    axis = np.linspace(.32, .67, 8, dtype=np.float32)
    points = np.stack(np.meshgrid(axis, axis, axis, indexing='ij'), axis=-1).reshape(-1,3)
    velocity = np.zeros_like(points); velocity[:,0] = 1
    sim.load_marker_particle_data(len(points), points.astype('<f4').tobytes(), velocity.astype('<f4').tobytes())
    sim.initialize()
    def read_positions():
        return np.array([[v.x,v.y,v.z] for v in sim.get_marker_particle_positions()])
    before = read_positions()
    sim.update(0.0)
    after = read_positions()
    stats = sim.get_frame_stats_data()
    surface = sim.get_surface_data()
    (a.output/'synthetic-zero-dt.bobj').write_bytes(surface)
    result['synthetic_zero_dt'] = dict(input_particles=len(points), particles_before=len(before), particles_after=len(after),
        effective_delta_time=float(stats.delta_time), frame=int(stats.frame), surface_bytes=len(surface),
        exact_position_array_unchanged=bool(before.shape == after.shape and np.array_equal(before,after)),
        status='REJECTED_AS_STATE_PRESERVING_EXTRACTION' if stats.delta_time != 0 else 'REQUIRES_FURTHER_VALIDATION')
    if before.shape == after.shape:
        left = before[np.lexsort(before.T[::-1])]; right = after[np.lexsort(after.T[::-1])]
        result['synthetic_zero_dt']['sorted_position_max_delta_m'] = float(np.linalg.norm(left-right,axis=1).max())
    (a.output/'probe.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
