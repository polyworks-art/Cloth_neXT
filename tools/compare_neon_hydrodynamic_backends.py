"""Measure scene-level CPU/CUDA differences for completed matching NEON cases.

This reports differences without inventing a production acceptance threshold.
"""
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cpu',type=Path,required=True)
    p.add_argument('--cuda',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    left=json.loads((a.cpu/'report.json').read_text())
    right=json.loads((a.cuda/'report.json').read_text())
    for key in ('source_scene_sha256','frames','quality','saved_zones'):
        if left[key]!=right[key]:raise ValueError(f'Incompatible {key}')
    lc={c['label']:c for c in left['cases']};rc={c['label']:c for c in right['cases']}
    records=[]
    for label in sorted(lc.keys() & rc.keys()):
        cpu=Path(lc[label].get('artifact_root',a.cpu/label))
        cuda=Path(rc[label].get('artifact_root',a.cuda/label))
        for path in sorted(cpu.glob('S04_NEON_Garments*.npy')):
            x=np.load(path);y=np.load(cuda/path.name)
            assert x.shape==y.shape and x.shape[0]==left['frames']
            assert np.isfinite(x).all() and np.isfinite(y).all()
            delta=np.linalg.norm(x.astype(np.float64)-y.astype(np.float64),axis=-1)
            records.append(dict(case=label,object=path.stem,max_m=float(delta.max()),
                                rms_m=float(np.sqrt(np.mean(delta**2)))))
    if not records:raise ValueError('No completed matching cases')
    result=dict(scope='Completed matching full native NEON trajectories',
                cpu_solver_sha256=left['solver_sha256'],cuda_solver_sha256=right['solver_sha256'],
                acceptance='Measured differences; no production threshold claimed',cases=records)
    a.output.write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))


if __name__=='__main__':main()
