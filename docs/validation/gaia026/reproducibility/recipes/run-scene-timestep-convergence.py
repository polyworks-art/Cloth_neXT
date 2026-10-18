"""Timestep convergence of a non-hydro benchmark scene with the fast exact solver.

Determinism is a precondition here (every run is bit-reproducible), so the
differences measured are discretisation only. Copies the scene, changes only
param.toml `dt`, runs each dt twice (the pair must be byte-identical) and
compares successive halvings on the output frames. Run from the repo root;
takes the shared GPU lock.
"""
import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path
import numpy as np

root = Path.cwd(); B = root/'.hydro-test-tmp/reproducibility-20261017'
worker = Path(os.environ.get('PPF_BENCH_WORKER', B/'native-source/target/research-cuda/release/ppf-contact-solver.exe'))
frozen_bin = root/'.hydro-test-tmp/blender-e2e-20261016/runtime/bin'
FAST = dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all', PPF_REPRO_FILTERED_CONTACT='1',
            PPF_REPRO_SERIAL_CSR='1', PPF_REPRO_SERIAL_STITCH='1', PPF_REPRO_SERIAL_DIRICHLET='1',
            PPF_REPRO_FAST_CONTACT='1', PPF_REPRO_FAST_SERIAL='1')
LOCK = [sys.executable, str(Path(__file__).with_name('gpu_lock.py'))]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scene', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--dt', type=float, nargs='+', required=True); p.add_argument('--lib', type=Path, default=B/'fast-v4')
    a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=False)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PPF_REPRO_', 'PPF_EXPERIMENTAL_HYDRO'))}
    env.update(FAST); env['PATH'] = str(a.lib)+os.pathsep+str(frozen_bin)+os.pathsep+env['PATH']
    runs = {}
    subprocess.run(LOCK+['acquire', f'timestep convergence {a.scene.name}'], check=True)
    try:
        for dt in a.dt:
            fixture = a.output/f'dt-{dt:g}/session'; shutil.copytree(a.scene, fixture)
            text = (fixture/'param.toml').read_text()
            text, n = re.subn(r'(?m)^dt = .*$', f'dt = {dt!r}', text); assert n == 1
            (fixture/'param.toml').write_text(text)
            for rep in ('a', 'b'):
                out = a.output/f'dt-{dt:g}/{rep}'
                with (a.output/f'dt-{dt:g}/{rep}.log').open('w') as log:
                    code = subprocess.run([str(worker), '--path', str(fixture), '--output', str(out)], env=env,
                                          stdout=log, stderr=subprocess.STDOUT).returncode
                assert code == 0, (dt, rep, code)
            runs[dt] = a.output/f'dt-{dt:g}'
            print('dt', dt, 'done', flush=True)
    finally:
        subprocess.run(LOCK+['release'])
    frames = sorted(int(q.stem.split('_')[1]) for q in (runs[a.dt[0]]/'a').glob('vert_*.bin'))
    load = lambda d, rep, i: np.fromfile(runs[d]/rep/f'vert_{i}.bin', '<f4').reshape(-1, 3).astype(float)
    result = dict(scene=str(a.scene), dts=a.dt, frames=len(frames), repeat_identical={}, pairs=[])
    for dt in a.dt:
        result['repeat_identical'][str(dt)] = all((runs[dt]/'a'/f'vert_{i}.bin').read_bytes() == (runs[dt]/'b'/f'vert_{i}.bin').read_bytes() for i in frames)
    first = load(a.dt[0], 'a', frames[0]); last = load(a.dt[0], 'a', frames[-1]); moving = np.linalg.norm(last-first, axis=1) > 0
    for x, y in zip(a.dt, a.dt[1:]):
        per = []
        for i in frames:
            d = np.linalg.norm(load(x, 'a', i)-load(y, 'a', i), axis=1)[moving]
            per.append(dict(frame=i, max_m=float(d.max()), rms_m=float(np.sqrt((d**2).mean()))))
        result['pairs'].append(dict(coarse=x, fine=y, max_m=max(f['max_m'] for f in per), final_rms_m=per[-1]['rms_m'], frames=per))
    (a.output/'convergence.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'pairs'}), [(q['coarse'], q['fine'], q['max_m'], q['final_rms_m']) for q in result['pairs']])


if __name__ == '__main__':
    main()
