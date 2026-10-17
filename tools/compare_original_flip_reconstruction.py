"""Compare an original-mesher replay with an independent cached BOBJ reference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.audit_neon_hydrodynamic_loads import read_surface


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--case', type=Path, required=True)
    p.add_argument('--dependencies', required=True)
    a = p.parse_args()
    sys.path.insert(0,a.dependencies)
    import trimesh
    from scipy.spatial import cKDTree
    x, f, sha = read_surface(a.reference)
    y, g, replay_sha = read_surface(a.case/'reconstructed.bobj')
    source = json.loads((a.case/'input-provenance.json').read_text())
    tolerance = source['predeclared_geometry_gate']['maximum_vertex_correspondence_error_m']
    result = dict(frame=source['frame'], reference_sha256=sha, replay_sha256=replay_sha,
        reference_vertices=len(x), replay_vertices=len(y), reference_triangles=len(f), replay_triangles=len(g),
        same_face_array=bool(f.shape==g.shape and np.array_equal(f,g)), tolerance_m=tolerance,
        byte_identical=sha==replay_sha)
    if x.shape == y.shape:
        delta = np.linalg.norm(x-y,axis=1)
        result['indexed_vertex_delta_m'] = dict(maximum=float(delta.max()), rms=float(np.sqrt(np.mean(delta**2))),
                                               p99=float(np.quantile(delta,.99)))
    direct = result['same_face_array'] and x.shape == y.shape and result['indexed_vertex_delta_m']['maximum']<=tolerance
    if direct:
        result['mapped_topology_identical'] = True
        result['geometry_gate'] = 'PASS'
        result['surface_bound'] = 'Identical indexed triangles; maximum vertex delta also bounds every corresponding barycentric surface point'
    else:
        distance, indices = cKDTree(x).query(y,workers=4)
        reverse, _ = cKDTree(y).query(x,workers=4)
        bijective = len(x)==len(y) and len(np.unique(indices))==len(x)
        mapped_equal = False
        if bijective and len(f)==len(g):
            one=np.sort(f,axis=1);two=np.sort(indices[g],axis=1)
            one=one[np.lexsort(one.T[::-1])];two=two[np.lexsort(two.T[::-1])]
            mapped_equal=bool(np.array_equal(one,two))
        maximum = max(float(distance.max()),float(reverse.max()))
        result.update(bidirectional_vertex_bound_m=maximum,bijective_vertex_map=bijective,mapped_topology_identical=mapped_equal,
                      geometry_gate='PASS' if mapped_equal and maximum<=tolerance else 'NO_GO')
    for label,vertices,faces in [('reference',x,f),('replay',y,g)]:
        mesh=trimesh.Trimesh(vertices,faces,process=False)
        counts=np.bincount(mesh.edges_unique_inverse)
        result[label+'_topology']=dict(watertight=bool(mesh.is_watertight), winding=bool(mesh.is_winding_consistent),
            components=int(mesh.body_count),open_edges=int((counts==1).sum()),nonmanifold_edges=int((counts>2).sum()),
            zero_area_triangles=int((mesh.area_faces==0).sum()),volume_m3=float(mesh.volume))
    result['hydrodynamic_transport_approved'] = False
    (a.case/'geometry-comparison.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__ == '__main__':main()
