"""Verify exported NEON cloth trajectories remain inside the prepared SDF crop."""
import argparse
import json
from pathlib import Path
import numpy as np


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    a=p.parse_args()
    manifest=json.loads(a.manifest.read_text())
    report=json.loads((a.input/'report.json').read_text())
    surfaces=manifest['provenance']['surfaces']
    # All cached SDF domains must cover the trajectory. Blender -> solver x,z,-y.
    low=np.max([s['sdf_minimum_blender_m'] for s in surfaces],axis=0)
    high=np.min([s['sdf_maximum_blender_m'] for s in surfaces],axis=0)
    solver_low=low[[0,2,1]].copy();solver_low[2]=-high[1]
    solver_high=high[[0,2,1]].copy();solver_high[2]=-low[1]
    records=[]
    for case in report['cases']:
        root=Path(case.get('artifact_root',a.input/case['label']))
        # Stored garment arrays were selected through the original vertex map.
        for path in sorted(root.glob('S04_NEON_Garments*.npy')):
            x=np.load(path,mmap_mode='r')
            assert np.isfinite(x).all()
            margin=float(min((x-solver_low).min(),(solver_high-x).min()))
            records.append(dict(case=case['label'],object=path.stem,
                                minimum_domain_margin_m=margin,passed=margin>1e-5))
    if not records:raise ValueError('No completed garment trajectories')
    result=dict(result='PASS' if all(r['passed'] for r in records) else 'FAIL',cases=records,
                scope='Exported cloth vertices; convex triangle quadrature and 1e-5 m side probes are covered. Intermediate Newton iterates are not audited.')
    (a.input/'sdf-roi-validation.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if result['result']!='PASS':raise SystemExit(1)


if __name__=='__main__':main()
