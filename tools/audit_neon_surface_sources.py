"""Read-only, resumable FLIP source audit. Never repairs source geometry."""
import argparse
import json
from pathlib import Path
import struct
import sys


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dependencies', required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--first', type=int, default=130)
    p.add_argument('--last', type=int, default=160)
    a = p.parse_args()
    sys.path[:0] = [a.dependencies, str(Path(__file__).resolve().parents[1])]
    import numpy as np
    import trimesh
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from tools.audit_neon_hydrodynamic_loads import read_surface
    from cloth_next.gaia.particle_stream import vector_layout
    a.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for frame in range(a.first, a.last + 1):
        x, f, sha = read_surface(a.source / f'{frame:06}.bobj')
        mesh = trimesh.Trimesh(vertices=x, faces=f, process=False)
        edges = mesh.edges_unique
        counts = np.bincount(mesh.edges_unique_inverse)
        bad = edges[counts != 2]
        graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(len(x), len(x)))
        n, labels = connected_components(graph, directed=False)
        used = np.unique(labels[f.ravel()])
        defective = np.unique(labels[bad.ravel()]) if len(bad) else []
        bounds = json.loads((a.source / f'bounds{frame:06}.bbox').read_text())
        lo = np.array([bounds[k] for k in ('x', 'y', 'z')])
        hi = lo + [bounds[k] for k in ('width', 'height', 'depth')]
        regions = []
        if len(bad):
            bv, inv = np.unique(bad, return_inverse=True)
            be = inv.reshape(-1, 2)
            bg = coo_matrix((np.ones(len(be)), (be[:, 0], be[:, 1])), shape=(len(bv), len(bv)))
            _, bl = connected_components(bg, directed=False)
            for label in np.unique(bl):
                ids = bv[bl == label]
                boundary = edges[counts == 1]
                boundary = boundary[np.isin(boundary[:, 0], ids)]
                degree = np.bincount(boundary.ravel(), minlength=len(x))[ids]
                points = x[ids]
                regions.append(dict(minimum=points.min(0).tolist(), maximum=points.max(0).tolist(),
                    domain_distance_m=float(np.min(np.minimum(points-lo, hi-points))),
                    boundary_edges=len(boundary), boundary_loop=bool(len(boundary) and np.all(degree == 2)),
                    boundary_branch_vertices=int((degree > 2).sum()), boundary_endpoints=int((degree == 1).sum())))
        particles = {}
        for kind, prefix in [('positions', 'fluidparticles'), ('velocities', 'fluidparticlesvelocity')]:
            path = a.source / f'{prefix}{frame:06}.ffp3'
            try:
                layout = vector_layout(path)
                particles[kind] = dict(available=True, bytes=path.stat().st_size, layout=list(layout))
            except (OSError, ValueError) as exc:
                particles[kind] = dict(available=False, error=str(exc))
        row = dict(frame=frame, sha256=sha, vertices=len(x), triangles=len(f),
            components=len(used), closed_components=int(len(used)-len(defective)),
            watertight=bool(mesh.is_watertight), winding=bool(mesh.is_winding_consistent),
            volume_m3=float(mesh.volume), open_edges=int((counts == 1).sum()),
            nonmanifold_edges=int((counts > 2).sum()), degenerate_triangles=int((mesh.area_faces == 0).sum()),
            boundary_loops=sum(r['boundary_loop'] for r in regions), problem_regions=regions,
            domain=bounds, particles=particles)
        rows.append(row)
        a.output.write_text(json.dumps(rows, indent=2), encoding='utf-8')
        print(frame, row['open_edges'], row['nonmanifold_edges'], row['closed_components'], flush=True)


if __name__ == '__main__':
    main()
