"""Controlled timestep sensitivity with the deterministic (ordered) corrected solver.

Copies the unchanged NEON fixture, changes ONLY param.toml `dt`, runs the ordered
research worker and compares world-space material vertices with the bit-identical
dt=0.01 reference (full-corrected-cached-five/0). Run from the repo root.
The difference is discretization sensitivity, not an error against ground truth.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path
import numpy as np

root = Path.cwd(); B = root/'.hydro-test-tmp/reproducibility-20261017'
frozen_bin = root/'.hydro-test-tmp/blender-e2e-20261016/runtime/bin'
fluid = root/'.owner-test-tmp/20261017/published-2926-full-host/session-evidence/hydrodynamic-aa366355bde442c3b8f0c2412edd3447'
exe = B/'native-source/target/research-cuda/release/ppf-contact-solver.exe'; lib = B/'cached-cuda-build/lib'
REPRO = dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all', PPF_REPRO_FILTERED_CONTACT='1',
             PPF_REPRO_SERIAL_CSR='1', PPF_REPRO_SERIAL_STITCH='1')


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--dt', type=float, nargs='+', required=True); a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False)
    reference = B/'full-corrected-cached-five/0/output'; results = []
    for dt in a.dt:
        case = a.output/f'dt-{dt:g}'; fixture = case/'session'
        shutil.copytree(B/'neon-fixture/session', fixture)
        text = (fixture/'param.toml').read_text()
        new, count = re.subn(r'(?m)^dt = .*$', f'dt = {dt!r}', text); assert count == 1
        (fixture/'param.toml').write_text(new)
        env = {k: v for k, v in os.environ.items() if not k.startswith('PPF_REPRO_')}
        env.update(REPRO, PPF_EXPERIMENTAL_HYDRO_TEST_JSON=str(fluid/'model.json'), PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=str(fluid/'volumes'))
        env['PATH'] = str(lib)+os.pathsep+str(frozen_bin)+os.pathsep+env['PATH']
        start = time.perf_counter()
        with (case/'worker.log').open('w') as log:
            code = subprocess.run([str(exe), '--path', str(fixture), '--output', str(case/'output')], env=env,
                                  stdout=log, stderr=subprocess.STDOUT).returncode
        elapsed = time.perf_counter()-start
        frames = []
        for i in range(1, 30):
            x = np.fromfile(case/f'output/vert_{i}.bin', '<f4').reshape(-1, 3).astype(np.float64)
            y = np.fromfile(reference/f'vert_{i}.bin', '<f4').reshape(-1, 3).astype(np.float64)
            d = np.linalg.norm(x-y, axis=1)
            frames.append(dict(sample=i, max_m=float(d.max()), rms_m=float(np.sqrt((d**2).mean())), finite=bool(np.isfinite(x).all())))
        results.append(dict(dt=dt, exit_code=code, elapsed_seconds=elapsed, frames=frames,
                            max_m=max(f['max_m'] for f in frames), final_rms_m=frames[-1]['rms_m']))
        print({k: v for k, v in results[-1].items() if k != 'frames'}, flush=True)
        (a.output/'results.json').write_text(json.dumps(dict(
            scope='dt sensitivity vs dt=0.01 deterministic reference; all native vertices (cloth+collider); not ground-truth error',
            reference=str(reference), results=results), indent=2))


if __name__ == '__main__':
    main()
