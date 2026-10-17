"""Add a read-only proximity-weld witness to an isolated grid-edge mesher copy."""
from pathlib import Path
import shutil,json,hashlib,argparse
p=argparse.ArgumentParser();p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();base=a.baseline;out=a.output;out.mkdir(exist_ok=False,parents=True)
shutil.copytree(base/'source',out/'source')
for n in ('bridge.cpp','main.cpp','CMakeLists.txt'):shutil.copy2(base/n,out/n)
e=out/'source/src/engine';h=e/'trianglemesh.h';s=h.read_text();s=s.replace('public:','public:\n    std::vector<std::pair<int,int>> cnxJoinWitness(TriangleMesh &other);',1);h.write_text(s)
f=e/'trianglemesh.cpp';s=f.read_text();s+='''
std::vector<std::pair<int,int>> TriangleMesh::cnxJoinWitness(TriangleMesh &other) {
    TriangleMesh copy=*this;
    double tolerance=10e-5;
    AABB bbox=copy._getMeshVertexIntersectionAABB(copy.vertices,other.vertices,tolerance);
    unsigned int split=copy.vertices.size(); copy.append(other);
    std::vector<int> first,second;
    for(unsigned int i=0;i<copy.vertices.size();i++) if(bbox.isPointInside(copy.vertices[i])) (i<split?first:second).push_back(i);
    std::vector<std::pair<int,int>> pairs;
    copy._findDuplicateVertexPairs(first,second,bbox,tolerance,pairs);
    return pairs;
}
''';f.write_text(s)
f=e/'particlemesher.cpp';s=f.read_text().replace('#include <map>','#include <map>\n#include <iostream>');s=s.replace('    std::map<std::array<int,4>, int> cnxGlobalEdges;','    std::map<std::array<int,4>, int> cnxGlobalEdges;\n    std::vector<std::array<int,4>> cnxGlobalKeys;')
s=s.replace('        std::vector<int> remap(chunkMesh.vertices.size());','''        if(!mesh.vertices.empty() && !chunkMesh.vertices.empty()) {
            auto pairs=mesh.cnxJoinWitness(chunkMesh);
            for(auto pair:pairs) {
                auto first=cnxGlobalKeys.at(pair.first);
                auto second=_cnxChunkEdges.at(pair.second-mesh.vertices.size());
                if(first!=second) {
                    std::cout<<"CNX_FALSE_PROXIMITY_WELD chunk="<<i<<" first=";
                    for(auto value:first) std::cout<<value<<",";
                    std::cout<<" second=";for(auto value:second) std::cout<<value<<",";
                    std::cout<<std::endl;
                }
            }
        }
        std::vector<int> remap(chunkMesh.vertices.size());''')
s=s.replace('if(result.second) mesh.vertices.push_back(chunkMesh.vertices[n]);','if(result.second) { mesh.vertices.push_back(chunkMesh.vertices[n]); cnxGlobalKeys.push_back(key); }');f.write_text(s)
(out/'witness-provenance.json').write_text(json.dumps({'method':'Read-only shadow copy calls original proximity pair finder, reports matches between different canonical grid edges; candidate mesh unchanged.', 'modified_sources':{n:hashlib.sha256((e/n).read_bytes()).hexdigest() for n in ('particlemesher.cpp','trianglemesh.h','trianglemesh.cpp')}},indent=2))
