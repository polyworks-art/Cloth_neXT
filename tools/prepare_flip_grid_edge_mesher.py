"""Isolated grid-edge identity join experiment; never patches installed FLIP."""
import argparse, hashlib, json, re, shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
a.output.mkdir(exist_ok=False,parents=True)
shutil.copytree(a.baseline/'source',a.output/'source')
for name in ('bridge.cpp','main.cpp','CMakeLists.txt','source-provenance.json'):shutil.copy2(a.baseline/name,a.output/name)
e=a.output/'source/src/engine'
def edit(name,fn):
 f=e/name;s=f.read_text();t=fn(s);assert t!=s,name;f.write_text(t)
edit('polygonizer3d.h',lambda s:s.replace('private:', '    using CnxEdge = std::array<int, 4>;\n    std::vector<CnxEdge> cnxEdges;\nprivate:',1).replace('#include "threadutils.h"','#include <array>\n#include "threadutils.h"'))
def poly(s):
 pattern=r'meshVertices.push_back\(v\);\s*edges\.([UVW])\.set\(vertices\[(\d)\],'
 def sub(m):
  axis={'U':0,'V':1,'W':2}[m[1]];n=m[2]
  return f'meshVertices.push_back(v);\n            cnxEdges.push_back({{vertices[{n}].i, vertices[{n}].j, vertices[{n}].k, {axis}}});\n            edges.{m[1]}.set(vertices[{n}],'
 s,n=re.subn(pattern,sub,s);assert n==12,n
 return s
edit('polygonizer3d.cpp',poly)
edit('particlemesher.h',lambda s:s.replace('#include <vector>','#include <vector>\n#include <array>').replace('private:', 'private:\n    std::vector<std::array<int,4>> _cnxChunkEdges;',1))
def mesher(s):
 s=s.replace('#include "particlemesher.h"','#include "particlemesher.h"\n#include <map>\n#include <stdexcept>')
 s=s.replace('    TriangleMesh mesh;\n    for (size_t i = 0;', '    TriangleMesh mesh;\n    std::map<std::array<int,4>, int> cnxGlobalEdges;\n    for (size_t i = 0;',1)
 s=s.replace('        mesh.join(chunkMesh);','''        if (_cnxChunkEdges.size() != chunkMesh.vertices.size()) throw std::runtime_error("Grid edge identity count mismatch");
        std::vector<int> remap(chunkMesh.vertices.size());
        for (size_t n=0;n<chunkMesh.vertices.size();n++) {
            auto key=_cnxChunkEdges[n];
            auto result=cnxGlobalEdges.emplace(key, (int)mesh.vertices.size());
            remap[n]=result.first->second;
            if(result.second) mesh.vertices.push_back(chunkMesh.vertices[n]);
        }
        for(auto triangle:chunkMesh.triangles) {
            for(int n=0;n<3;n++) triangle.tri[n]=remap[triangle.tri[n]];
            mesh.triangles.push_back(triangle);
        }''',1)
 s=s.replace('    ScalarFieldData fieldData;\n    _initializeScalarFieldData(chunk', '    _cnxChunkEdges.clear();\n    ScalarFieldData fieldData;\n    _initializeScalarFieldData(chunk',1)
 s=s.replace('    m.translate(chunk.positionOffset);','''    _cnxChunkEdges = polygonizer.cnxEdges;
    for(auto &key:_cnxChunkEdges) { key[0]+=chunk.minGridIndex.i; key[1]+=chunk.minGridIndex.j; key[2]+=chunk.minGridIndex.k; }
    m.translate(chunk.positionOffset);''',1)
 return s
edit('particlemesher.cpp',mesher)
(a.output/'candidate-provenance.json').write_text(json.dumps({'method':'Canonical original Marching Cubes grid-edge identity; same 3 chunks and original fields/interpolation/smoothing. Experimental, no transport approval.', 'baseline':str(a.baseline.resolve()),'modified_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in e.iterdir() if f.name in ('polygonizer3d.h','polygonizer3d.cpp','particlemesher.h','particlemesher.cpp')}},indent=2))
