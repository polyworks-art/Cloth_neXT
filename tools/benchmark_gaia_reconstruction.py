"""Compare the original ordered splat with production using actual FLIP files."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.particle_stream import ParticleFileSample
from cloth_next.gaia.preparation import source_stamp
from cloth_next.gaia.water_field import WaterVelocityField, reconstruct_chunks


def reference_reconstruct(chunks, minimum, maximum, dimensions, *, support_threshold=1., max_speed=None):
    """Frozen pre-optimization arithmetic and accumulation order."""
    lo,hi=np.asarray(minimum,float),np.asarray(maximum,float)
    dims=np.asarray(dimensions,int)
    density=np.zeros(int(np.prod(dims)))
    momentum=np.zeros((len(density),3))
    source_count=0
    for pp,vv in chunks:
        pp,vv=np.asarray(pp,float),np.asarray(vv,float)
        source_count+=len(pp)
        if max_speed is not None:
            speed=np.linalg.norm(vv,axis=1)
            vv=vv*np.minimum(1.,max_speed/np.maximum(speed,1e-30))[:,None]
        inside=np.all((pp>=lo)&(pp<=hi),axis=1)
        pp,vv=pp[inside],vv[inside]
        q=(pp-lo)/(hi-lo)*(dims-1)
        base=np.minimum(np.floor(q).astype(int),dims-2)
        frac=q-base
        for z in (0,1):
            for y in (0,1):
                for x in (0,1):
                    corner=np.asarray((x,y,z))
                    weight=np.prod(np.where(corner,frac,1-frac),axis=1)
                    index=base+corner
                    flat=(index[:,2]*dims[1]+index[:,1])*dims[0]+index[:,0]
                    np.add.at(density,flat,weight)
                    np.add.at(momentum,flat,vv*weight[:,None])
    velocity=np.divide(momentum,density[:,None],out=np.zeros_like(momentum),where=density[:,None]>1e-12)
    shape=tuple(dims[::-1])
    return WaterVelocityField(tuple(lo),tuple(hi),velocity.reshape(*shape,3).astype('<f4'),
        np.minimum(1.,density/support_threshold).reshape(shape).astype('<f4'),source_count)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gaia',type=Path,required=True)
    p.add_argument('--frames',type=int,nargs='+',default=[1])
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    with GaiaContainer(args.gaia) as cache: metadata=cache.manifest['water']
    provider=metadata['provider'];root=Path(provider['cache']);results=[]
    for frame in args.frames:
        bounds_path=root/f'bounds{frame:06d}.bbox'
        raw=json.loads(bounds_path.read_text())
        sample=ParticleFileSample(root/f'fluidparticles{frame:06d}.ffp3',root/f'fluidparticlesvelocity{frame:06d}.ffp3',
            tuple(map(tuple,provider['cache_matrix'])),tuple(float(np.float32(raw[key])) for key in ('x','y','z')))
        paths=(sample.positions,sample.velocities,bounds_path)
        stamps=tuple(source_stamp(path) for path in paths)
        options=dict(support_threshold=metadata['grid_layout']['support_threshold'],max_speed=None)
        fields={};timings={}
        for label,method in (('reference',reference_reconstruct),('optimized',reconstruct_chunks)):
            start=time.perf_counter()
            fields[label]=method(sample.chunks(),*provider['bounds'],metadata['dimensions'],**options)
            timings[label]=time.perf_counter()-start
        result=dict(frame=frame,particles=fields['reference'].source_particle_count,seconds=timings,
            velocity_exact=bool(np.array_equal(fields['reference'].velocity,fields['optimized'].velocity)),
            influence_exact=bool(np.array_equal(fields['reference'].influence,fields['optimized'].influence)),
            source_unchanged=stamps==tuple(source_stamp(path) for path in paths))
        assert result['velocity_exact'] and result['influence_exact'] and result['source_unchanged']
        results.append(result)
        print(json.dumps(result),flush=True)
    args.output.write_text(json.dumps(dict(result='PASS',cases=results),indent=2))


if __name__=='__main__':main()
