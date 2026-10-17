"""Reject/characterize an unmodified-radius particle union; no solver integration."""
import json
from pathlib import Path
import sys
import argparse


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dependencies', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    sys.path[:0] = [a.dependencies, str(Path(__file__).resolve().parents[1])]
    import numpy as np
    import trimesh
    from scipy.spatial import cKDTree
    from cloth_next.gaia.particle_stream import vector_layout
    from tools.audit_neon_hydrodynamic_loads import read_surface
    root = Path('F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache/bakefiles')
    rows = []
    for frame in (130, 131, 132, 133, 135, 160):
        x, f, sha = read_surface(root / f'{frame:06}.bobj')
        bounds = json.loads((root / f'bounds{frame:06}.bbox').read_text())
        counts, offset = vector_layout(root / f'fluidparticles{frame:06}.ffp3')
        particle = np.memmap(root / f'fluidparticles{frame:06}.ffp3', dtype='<f4', mode='r', offset=offset).reshape(-1, 3)
        velocity = np.memmap(root / f'fluidparticlesvelocity{frame:06}.ffp3', dtype='<f4', mode='r', offset=offset).reshape(-1, 3)
        radius = bounds['dx'] * (3/(32*np.pi)) ** (1/3) * 3
        tree = cKDTree(particle)
        # Deterministic full-surface coverage; no tuning radius to reference.
        points = x[np.linspace(0, len(x)-1, min(len(x), 20000), dtype=int)]
        distance, ids = tree.query(points, workers=4)
        error = distance-radius
        row = dict(frame=frame, bobj_sha256=sha, particles=len(particle),
            radius_m=radius, surface_sample_count=len(points),
            bobj_on_particle_union_residual_m=dict(mean=float(error.mean()), rms=float(np.sqrt(np.mean(error**2))),
                p95_abs=float(np.quantile(abs(error), .95)), maximum_abs=float(abs(error).max())),
            local_nearest_particle_speed_m_s=dict(mean=float(np.linalg.norm(velocity[ids], axis=1).mean()),
                maximum=float(np.linalg.norm(velocity[ids], axis=1).max())),
            valid_reference=bool(trimesh.Trimesh(x, f, process=False).is_watertight),
            status='NOT_VALIDATED_FOR_TRANSPORT',
            missing_equivalence=['original obstacle SDF/meshing offset', 'original smoothing and grid polygonization',
                                 'cloth wet area and hydrodynamic force validation', 'resolution convergence'])
        rows.append(row)
        a.output.write_text(json.dumps(rows, indent=2))
        print(json.dumps(row), flush=True)
        del tree, particle, velocity


if __name__ == '__main__':
    main()
