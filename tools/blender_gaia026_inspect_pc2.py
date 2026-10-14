"""Independent discrete surface checks on existing real PC2 artifacts."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np
from mathutils.bvhtree import BVHTree

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--repo',type=Path,required=True)
p.add_argument('--matrix',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(a.repo))
from tools.gaia026_geometry_checks import triangles_pierce
from cloth_next.bake.pc2 import iter_frames
matrix=json.loads(a.matrix.read_text())
results=[]
for case in matrix['cases']:
    if case['result']!='PASS':continue
    report=json.loads(Path(case['report']).read_text())
    nx,ny={1000:(25,40),5000:(50,100),20000:(100,200),50000:(200,250)}[case['vertices_per_cloth']]
    triangles=[]
    for j in range(ny-1):
        for i in range(nx-1):
            k=j*nx+i;triangles.extend(((k,k+1,k+nx+1),(k,k+nx+1,k+nx)))
    triangles=np.asarray(triangles,dtype=int)
    for obj in report['objects']:
        if not Path(obj['pc2']).is_file():
            results.append(dict(label=case['label'],object=obj['object'],result='UNAVAILABLE',
                                reason='Earlier rebake replaced original file; retained scalar validation exists'))
            continue
        samples=np.asarray(list(iter_frames(Path(obj['pc2']))),dtype=float)
        assert np.isfinite(samples).all()
        checks=[]
        for index in sorted(set([0,len(samples)//2,len(samples)-1])):
            vertices=samples[index]
            tree=BVHTree.FromPolygons(vertices.tolist(),triangles.tolist(),all_triangles=True)
            pairs=[(x,y) for x,y in tree.overlap(tree) if x<y and not set(triangles[x]).intersection(triangles[y])]
            pierced=sum(triangles_pierce(vertices[triangles[x]],vertices[triangles[y]]) for x,y in pairs)
            checks.append(dict(frame=index+1,nonadjacent_overlap_candidates=len(pairs),strict_triangle_piercings=pierced))
        results.append(dict(label=case['label'],object=obj['object'],checks=checks,
                            result='PASS' if not any(x['strict_triangle_piercings'] for x in checks) else 'FAIL'))
a.output.write_text(json.dumps(dict(result='PASS' if all(x['result']!='FAIL' for x in results) else 'FAIL',
    cases=results,scope='Discrete first/middle/final-frame triangle crossings, not a continuous collision proof'),indent=2))
assert all(x['result']!='FAIL' for x in results)
