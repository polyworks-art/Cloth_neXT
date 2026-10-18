"""Checkpoint/resume bit-reproducibility with the fast deterministic path (NEON).

Takes a finished uninterrupted fast run (its output holds the solver's own
state_<N>.bin.gz checkpoints), copies the output up to checkpoint N into a new
folder, resumes with the worker's `--load N`, and byte-compares every later
vertex/telemetry frame with the uninterrupted run. Run from the repo root; takes
the shared GPU lock.
"""
import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path

root = Path.cwd(); B = root/'.hydro-test-tmp/reproducibility-20261017'
frozen_bin = root/'.hydro-test-tmp/blender-e2e-20261016/runtime/bin'
fluid = root/'.owner-test-tmp/20261017/published-2926-full-host/session-evidence/hydrodynamic-aa366355bde442c3b8f0c2412edd3447'
FAST = dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all', PPF_REPRO_FILTERED_CONTACT='1',
            PPF_REPRO_SERIAL_CSR='1', PPF_REPRO_SERIAL_STITCH='1', PPF_REPRO_SERIAL_DIRICHLET='1',
            PPF_REPRO_WALK_SPMV='1', PPF_REPRO_FAST_CONTACT='1', PPF_REPRO_FAST_SERIAL='1')
LOCK = [sys.executable, str(Path(__file__).with_name('gpu_lock.py'))]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, default=B/'neon-fixture/session')
    p.add_argument('--worker', type=Path, default=B/'worker-v2/ppf-contact-solver.exe')
    p.add_argument('--lib', type=Path, default=B/'fast-v4')
    p.add_argument('--output', type=Path, required=True); p.add_argument('--load', type=int, default=19)
    a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=False)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PPF_REPRO_', 'PPF_EXPERIMENTAL_HYDRO'))}
    env.update(FAST, PPF_EXPERIMENTAL_HYDRO_TEST_JSON=str(fluid/'model.json'), PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=str(fluid/'volumes'))
    env['PATH'] = str(a.lib)+os.pathsep+str(frozen_bin)+os.pathsep+env['PATH']
    full, resumed = a.output/'uninterrupted', a.output/'resumed'
    subprocess.run(LOCK+['acquire', 'NEON checkpoint resume determinism'], check=True)
    try:
        with (a.output/'uninterrupted.log').open('w') as log:
            assert subprocess.run([str(a.worker), '--path', str(a.fixture), '--output', str(full)], env=env,
                                  stdout=log, stderr=subprocess.STDOUT).returncode == 0
        assert (full/f'state_{a.load}.bin.gz').is_file(), sorted(q.name for q in full.glob('state_*'))
        # The resumed run sees only what existed when checkpoint N was written.
        resumed.mkdir()
        for q in full.iterdir():
            m = re.match(r'(vert|hydro|statistics)_(\d+)\.', q.name)
            if m and int(m[2]) > a.load: continue
            if q.name.startswith('state_') and q.name != f'state_{a.load}.bin.gz': continue
            if q.name == 'finished.txt': continue
            (shutil.copytree if q.is_dir() else shutil.copy2)(q, resumed/q.name)
        with (a.output/'resumed.log').open('w') as log:
            code = subprocess.run([str(a.worker), '--path', str(a.fixture), '--output', str(resumed), '--load', str(a.load)],
                                  env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    finally:
        subprocess.run(LOCK+['release'])
    later = sorted(q.name for q in full.iterdir() if (m := re.match(r'(vert|hydro)_(\d+)\.bin$', q.name)) and int(m[2]) > a.load)
    differing = [n for n in later if not (resumed/n).is_file() or (resumed/n).read_bytes() != (full/n).read_bytes()]
    result = dict(load=a.load, resumed_exit=code, compared=len(later), differing=differing, bit_identical=code == 0 and not differing)
    (a.output/'result.json').write_text(json.dumps(result, indent=2)); print(result)


if __name__ == '__main__':
    main()
