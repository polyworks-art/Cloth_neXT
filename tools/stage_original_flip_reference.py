"""Stage verified cached inputs for the read-only original FLIP mesher."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.gaia.particle_stream import vector_layout


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--cache', type=Path, required=True)
    p.add_argument('--frame', type=int, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--compute-chunks', type=int, choices=(1,3),
                   help='Controlled compute partition experiment; all physical meshing parameters stay unchanged')
    a = p.parse_args()
    a.cache, a.output = a.cache.resolve(), a.output.resolve()
    a.output.mkdir(parents=True, exist_ok=False)
    sim_path = a.cache/'export/flipdata.sim'
    data = json.loads(sim_path.read_text())
    init, surface = data['domain_data']['initialize'], data['domain_data']['surface']
    bounds = json.loads((a.cache/f'bakefiles/bounds{a.frame:06}.bbox').read_text())
    origin = np.array([bounds[k] for k in ('x','y','z')], dtype=np.float32)
    assert init['scale'] == 1 and all(not surface[k]['is_animated'] for k in (
        'particle_scale','smoothing_iterations','smoothing_value','subdivisions','enable_meshing_offset'))
    assert surface['meshing_volume_mode']['data'] == 'MESHING_VOLUME_MODE_DOMAIN'
    assert surface['enable_meshing_offset']['data'] and not surface['remove_mesh_near_domain']['data']
    assert surface['obstacle_meshing_mode']['data'] == 'MESHING_MODE_INSIDE_SURFACE'
    assert len(data['obstacle_data']) == 1
    obstacle = data['obstacle_data'][0]
    assert not obstacle['is_inversed']['data'] and obstacle['is_enabled']['data'] and obstacle['mesh_expansion']['data'] == 0
    with sqlite3.connect((a.cache/'export/export_data.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        row = c.execute('select object_id,object_motion_type from object where object_name=?',(obstacle['name'],)).fetchone()
        assert row and row[1] == 'STATIC'
        raw = c.execute('select mesh_static_data from mesh_static where object_id=?',(row[0],)).fetchone()[0]
    nv = struct.unpack_from('<i',raw)[0]
    vertices = np.frombuffer(raw,dtype='<f4',count=3*nv,offset=4).reshape(-1,3)
    local = (vertices-origin).astype('<f4')
    (a.output/'obstacle-local.bobj').write_bytes(raw[:4]+local.tobytes()+raw[4+12*nv:])
    fingerprints = {}
    for prefix, filename in [('fluidparticles','positions.bin'),('fluidparticlesvelocity','velocities.bin')]:
        path = a.cache/f'bakefiles/{prefix}{a.frame:06}.ffp3'
        counts, offset = vector_layout(path)
        samples = np.memmap(path,dtype='<f4',mode='r',offset=offset).reshape(-1,3)
        assert len(samples) == sum(counts[:3]) and np.isfinite(samples).all()
        # FluidSimulation.loadMarkerParticleData accepts WORLD coordinates and
        # performs domain-offset/scale conversion itself (_loadMarkerParticles).
        # Static obstacle meshes, in contrast, enter MeshObject in local space.
        staged = samples
        staged.tofile(a.output/filename)
        with path.open('rb') as f:fingerprints[path.name]=hashlib.file_digest(f,'sha256').hexdigest()
        del samples, staged
    settings = [bounds[k] for k in ('isize','jsize','ksize','dx')]+[
        surface['subdivisions']['data']+1, a.compute_chunks or surface['compute_chunks_auto'],
        surface['native_particle_scale']*surface['particle_scale']['data'],
        surface['smoothing_value']['data'], surface['smoothing_iterations']['data'], .25, *origin.tolist()]
    (a.output/'settings.txt').write_text(' '.join(map(str,settings)))
    report = dict(frame=a.frame, settings=settings, source_particle_sha256=fingerprints,
        original_compute_chunks=surface['compute_chunks_auto'], compute_chunks=settings[5],
        physical_meshing_parameters_changed=False,
        particle_input_space='world; original load API normalizes once',
        obstacle_source_sha256=hashlib.sha256(raw).hexdigest(), source_mutated=False,
        reconstruction_grid_cell_m=bounds['dx']/(surface['subdivisions']['data']+1),
        predeclared_geometry_gate=dict(maximum_vertex_correspondence_error_m=1e-5,
            rationale='Conservative float32 world/local roundtrip budget; same 10 µm physical geometry bound used in existing native/PC2 checks. Not a production hydrodynamics tolerance.',
            topology='Try one-to-one vertex correspondence plus identical mapped triangle connectivity; otherwise report non-equivalence and measure independent surface/occupancy errors before transport.'))
    (a.output/'input-provenance.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__ == '__main__':main()
