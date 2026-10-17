"""Build a read-only mesher bridge from the installed Individual source archive.

No simulation update is exposed by the CLI. Source algorithms are unchanged;
one explicit member bridge selects existing geometry/meshing methods only.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import zipfile

BRIDGE = r'''
#include "fluidsimulation.h"
#include <stdexcept>
#include <cstring>
void FluidSimulation::cnxLoadExactCachedParticles(const char *p, const char *v, size_t count) {
    if (!_isSimulationInitialized || !_markerParticles.empty()) throw std::runtime_error("Expected initialized empty particle state");
    std::vector<vmath::vec3> *positions, *velocities;
    _markerParticles.getAttributeValues("POSITION", positions);
    _markerParticles.getAttributeValues("VELOCITY", velocities);
    positions->reserve(count); velocities->reserve(count);
    for(size_t i=0;i<count;i++) {
        vmath::vec3 world, velocity;
        std::memcpy(&world,p+12*i,12); std::memcpy(&velocity,v+12*i,12);
        positions->push_back((world-_domainOffset)/_domainScale);
        velocities->push_back(velocity);
    }
    _markerParticles.update();
}
void FluidSimulation::cnxReconstructWithoutAdvance(const char *output) {
    if (!_isSimulationInitialized) throw std::runtime_error("Not initialized");
    // The cached obstacle is STATIC. dt is a geometry sampling interval only;
    // no pressure solve, particle update, forces or simulation step is called.
    _updateSolidLevelSet(1.0 / 30.0);
    std::vector<vmath::vec3> *original;
    _markerParticles.getAttributeValues("POSITION", original);
    std::vector<vmath::vec3> particles = *original;
    MeshLevelSet solid;
    solid.constructMinimalSignedDistanceField(_solidSDF);
    TriangleMesh surface, preview;
    _generateOutputSurface(surface, preview, &particles, &solid);
    surface.removeMinimumTriangleCountPolyhedra(_minimumSurfacePolyhedronTriangleCount);
    _removeMeshNearDomain(surface);
    _smoothSurfaceMesh(surface);
    _invertContactNormals(surface);
    surface.scale(vmath::vec3(_domainScale, _domainScale, _domainScale));
    surface.translate(_domainOffset);
    surface.writeMeshToBOBJ(output);
}
'''

MAIN = r'''
#include "fluidsimulation.h"
#include "meshobject.h"
#include <fstream>
#include <iostream>
#include <filesystem>
#include <cstring>
#include <stdexcept>
#include <algorithm>
namespace fs = std::filesystem;
std::vector<char> bytes(fs::path p) {
    std::ifstream f(p, std::ios::binary|std::ios::ate);
    if (!f) throw std::runtime_error("Cannot read input");
    size_t n = f.tellg(); f.seekg(0); std::vector<char> b(n);
    f.read(b.data(), n); if(!f) throw std::runtime_error("Truncated input"); return b;
}
int main(int argc, char **argv) { try {
    if(argc != 2) throw std::runtime_error("Expected isolated case folder");
    fs::path root(argv[1]); std::ifstream config(root/"settings.txt");
    int nx,ny,nz,sub,chunks,iters; double dx,scale,smoothing,offset,ox,oy,oz;
    if(!(config>>nx>>ny>>nz>>dx>>sub>>chunks>>scale>>smoothing>>iters>>offset>>ox>>oy>>oz))
        throw std::runtime_error("Invalid settings");
    auto p=bytes(root/"positions.bin"), v=bytes(root/"velocities.bin");
    if(p.size()!=v.size() || p.empty() || p.size()%12) throw std::runtime_error("Invalid particle inputs");
    FluidSimulation sim(nx,ny,nz,dx);
    sim.setDomainOffset(ox,oy,oz); sim.setDomainScale(1.0);
    sim.setSurfaceSubdivisionLevel(sub); sim.setNumPolygonizerSlices(chunks);
    sim.setMarkerParticleScale(scale); sim.setSurfaceSmoothingValue(smoothing);
    sim.setSurfaceSmoothingIterations(iters); sim.enableObstacleMeshingOffset();
    sim.setObstacleMeshingOffset(offset);
    TriangleMesh mesh;
    if(!mesh.loadBOBJ((root/"obstacle-local.bobj").string())) throw std::runtime_error("Cannot load obstacle");
    MeshObject obstacle(nx,ny,nz,dx); obstacle.updateMeshStatic(mesh); sim.addMeshObstacle(&obstacle);
    size_t count=p.size()/12;
    sim.initialize(); sim.cnxLoadExactCachedParticles(p.data(),v.data(),count);
    if(sim.getNumMarkerParticles()!=count) throw std::runtime_error("Input staging changed particle count");
    auto before=sim.getMarkerParticlePositions();
    // The public load API accepts world positions; this C++ getter returns
    // internal local positions. Verify exactly the source normalization.
    {
        auto world=reinterpret_cast<const vmath::vec3*>(p.data());
        for(size_t i=0;i<before.size();i++) {
            vmath::vec3 expected=(world[i]-vmath::vec3(ox,oy,oz))/1.0f;
            if(std::memcmp(&before[i],&expected,sizeof(vmath::vec3)))
                throw std::runtime_error("Input staging changed particle coordinates or order");
        }
    }
    auto beforeVelocity=sim.getMarkerParticleVelocities();
    if(std::memcmp(beforeVelocity.data(),v.data(),v.size())) throw std::runtime_error("Input staging changed velocities");
    std::cout << "CNX_INPUT_EXACT_POSITION_VELOCITY_PASS" << std::endl;
    sim.cnxReconstructWithoutAdvance((root/"reconstructed.bobj").string().c_str());
    auto after=sim.getMarkerParticlePositions();
    if(after.size()!=before.size() || std::memcmp(after.data(),before.data(),p.size()))
        throw std::runtime_error("Meshing changed particle state");
    auto afterVelocity=sim.getMarkerParticleVelocities();
    if(std::memcmp(afterVelocity.data(),beforeVelocity.data(),v.size())) throw std::runtime_error("Meshing changed velocities");
    std::cout << "CNX_READ_ONLY_MESHER_PASS particles=" << count << std::endl;
    return 0;
} catch(const std::exception &e) {std::cerr<<e.what()<<std::endl; return 1;} }
'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    a.output = a.output.resolve()
    source = a.output/'source'
    source.mkdir(parents=True, exist_ok=False)
    hashes = {}
    with zipfile.ZipFile(a.archive) as archive:
        prefix = '../../bl_flip_fluid/'
        for member in archive.infolist():
            if member.is_dir() or not member.filename.startswith(prefix):continue
            relative = PurePosixPath(member.filename[len(prefix):])
            if relative.is_absolute() or '..' in relative.parts:raise ValueError('Unsafe archive member')
            if not (str(relative).startswith('src/engine/') or str(relative).startswith('LICENSE_')):continue
            target = (source/str(relative)).resolve()
            if not target.is_relative_to(source):raise ValueError('Archive traversal')
            data = archive.read(member)
            target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(data)
            hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    engine = source/'src/engine'
    header = engine/'fluidsimulation.h'
    text = header.read_text()
    header.write_text(text.replace('public:', 'public:\n    void cnxReconstructWithoutAdvance(const char *output);\n    void cnxLoadExactCachedParticles(const char*, const char*, size_t);', 1))
    (a.output/'bridge.cpp').write_text(BRIDGE)
    (a.output/'main.cpp').write_text(MAIN)
    cmake = '''cmake_minimum_required(VERSION 3.20)
project(cnx_flip_readonly_mesher LANGUAGES CXX)
file(GLOB CORE "${CMAKE_CURRENT_SOURCE_DIR}/source/src/engine/*.cpp")
list(FILTER CORE EXCLUDE REGEX "/main.cpp$")
add_executable(cnx_flip_mesher ${CORE} bridge.cpp main.cpp source/src/engine/mixbox/mixbox_stub.cpp)
target_include_directories(cnx_flip_mesher PRIVATE source/src/engine)
target_compile_features(cnx_flip_mesher PRIVATE cxx_std_17)
target_compile_definitions(cnx_flip_mesher PRIVATE WITH_MIXBOX=0)
target_compile_options(cnx_flip_mesher PRIVATE -O3 -fopenmp -ffunction-sections -fdata-sections)
target_link_options(cnx_flip_mesher PRIVATE -static -static-libgcc -static-libstdc++ -fopenmp -Wl,--gc-sections)
'''
    (a.output/'CMakeLists.txt').write_text(cmake)
    (a.output/'source-provenance.json').write_text(json.dumps(dict(
        archive=str(a.archive.resolve()), archive_sha256=hashlib.sha256(a.archive.read_bytes()).hexdigest(),
        original_source_hashes=hashes, bridge=BRIDGE, note='Only a public bridge declaration added to copied header; original meshing algorithms unchanged. No simulation update.'), indent=2))
    subprocess.run(['cmake','-S',str(a.output),'-B',str(a.output/'build'),'-G','Ninja',
        '-DCMAKE_CXX_COMPILER='+shutil.which('g++')],check=True)
    subprocess.run(['cmake','--build',str(a.output/'build'),'--parallel','6'],check=True)


if __name__ == '__main__':main()
