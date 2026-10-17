"""Reconstruct selected native-cache frames from unchanged real FLIP particles."""
import hashlib
import json
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.particle_stream import ParticleFileSample
from cloth_next.gaia.water_field import reconstruct_chunks


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def main():
    cases=[]
    with GaiaContainer('F:/16_Cloth_neXt/cache_AD/S04_NEON_Garments_002_WaterFlow.gaia') as cache:
        water=cache.manifest['water'];provider=water['provider'];root=Path(provider['cache'])
        expected=dict(provider['sources'])
        for frame in (1,30,125,250):
            bounds=json.loads((root/f'bounds{frame:06}.bbox').read_text())
            p=root/f'fluidparticles{frame:06}.ffp3';v=root/f'fluidparticlesvelocity{frame:06}.ffp3'
            sample=ParticleFileSample(p,v,provider['cache_matrix'],tuple(float(np.float32(bounds[k])) for k in ('x','y','z')))
            speeds=[];positions=[]
            def chunks():
                for point,velocity in sample.chunks():
                    speeds.append(np.linalg.norm(velocity,axis=1));positions.append((point.min(0),point.max(0)))
                    yield point,velocity
            field=cache.frame(frame-1)
            rebuilt=reconstruct_chunks(chunks(),field.minimum,field.maximum,water['dimensions'],
                support_threshold=water['grid_layout']['support_threshold'],max_speed=None)
            speed=np.concatenate(speeds)
            paths=(p,v,root/f'bounds{frame:06}.bbox')
            case=dict(frame=frame,source_particle_count=len(speed),
                source_hashes_match=all(digest(path)==expected[path.name] for path in paths),
                source_speed_m_s=dict(mean=float(speed.mean()),p99=float(np.quantile(speed,.99)),max=float(speed.max())),
                max_velocity_reconstruction_error=float(np.max(np.abs(rebuilt.velocity-field.velocity))),
                max_occupancy_reconstruction_error=float(np.max(np.abs(rebuilt.influence-field.influence))))
            cases.append(case);print(case,flush=True)
            assert case['source_hashes_match']
            assert case['max_velocity_reconstruction_error']<1e-5
            assert case['max_occupancy_reconstruction_error']<1e-5
    (ROOT/'docs/validation/gaia026/coupling-investigation/source-reconstruction.json').write_text(json.dumps(dict(result='PASS',cases=cases),indent=2))


if __name__=='__main__':main()
