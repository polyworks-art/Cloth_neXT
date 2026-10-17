"""Compare saved full native reference solves from two backend builds."""
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
    cpu=json.loads((a.cpu/'report.json').read_text())
    cuda=json.loads((a.cuda/'report.json').read_text())
    if cpu.get('reference_variant','standard')!=cuda.get('reference_variant','standard'):
        raise ValueError('Reference variants differ')
    common=sorted({r['case'] for r in cpu['cases']} & {r['case'] for r in cuda['cases']})
    if not common:raise ValueError('No common completed cases')
    records=[]
    for case in common:
        x=np.load(a.cpu/case/'positions.npy')
        y=np.load(a.cuda/case/'positions.npy')
        f=np.load(a.cpu/case/'native-telemetry.npy')
        g=np.load(a.cuda/case/'native-telemetry.npy')
        assert x.shape==y.shape and f.shape==g.shape
        assert all(np.isfinite(v).all() for v in (x,y,f,g))
        dx=np.linalg.norm(x-y,axis=-1)
        # Native reduction order differs; compare physical trajectories and
        # integrated force rather than demanding identical fp32 bits.
        force_cpu=f[:,4:7].sum(0);force_cuda=g[:,4:7].sum(0)
        df=float(np.linalg.norm(force_cpu-force_cuda))
        force_limit=1e-5+1e-3*float(np.linalg.norm(force_cpu))
        record=dict(case=case,max_position_difference_m=float(dx.max()),
                    rms_position_difference_m=float(np.sqrt(np.mean(dx**2))),
                    net_force_difference_n=df,force_limit_n=force_limit,
                    position_limit_m=1e-5,
                    passed=bool(dx.max()<=1e-5 and df<=force_limit))
        records.append(record)
    result=dict(result='PASS' if all(r['passed'] for r in records) else 'FAIL',
                cpu_backend=cpu['backend'],cuda_backend=cuda['backend'],cases=records,
                scope='Full native solves and final native force telemetry; no direct Jacobian readback')
    a.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if result['result']!='PASS':raise SystemExit(1)


if __name__=='__main__':main()
