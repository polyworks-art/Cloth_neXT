"""Non-NEON contact benchmarks: unordered vs deterministic reference vs fast exact.

No hydrodynamics environment is set. Per scene: unordered (reference DLL, no
switches), reference (five PPF_REPRO_* switches), fast twice (plus
PPF_REPRO_FAST_CONTACT and PPF_REPRO_FAST_SERIAL). Reports runtime, contact
counts from the solver log, byte equality fast-vs-reference and fast-vs-fast,
and the unordered-vs-reference deviation. Acquires the shared GPU lock per scene.
Run from the repo root.
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from loaded_backend import loaded_cuda_backend

root = Path.cwd(); B = root/'.hydro-test-tmp/reproducibility-20261017'
frozen_bin = root/'.hydro-test-tmp/blender-e2e-20261016/runtime/bin'
worker = Path(os.environ.get('PPF_BENCH_WORKER', B/'native-source/target/research-cuda/release/ppf-contact-solver.exe'))
assert not (worker.parent/'libsimbackend_cuda.dll').exists()
# Reference = serial ascending order for every ordered pass, now including the
# Dirichlet lift (moving pins), which NEON never exercised with nonzero lifts.
REPRO = dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all', PPF_REPRO_FILTERED_CONTACT='1',
             PPF_REPRO_SERIAL_CSR='1', PPF_REPRO_SERIAL_STITCH='1', PPF_REPRO_SERIAL_DIRICHLET='1',
             **({'PPF_REPRO_WALK_SPMV': '1'} if os.environ.get('PPF_BENCH_WALK') else {}))
FAST = dict(REPRO, PPF_REPRO_FAST_CONTACT='1', PPF_REPRO_FAST_SERIAL='1')
CASES = [('unordered', B/'reference-deterministic', {}), ('reference', B/'fast-v3', REPRO),
         ('reference-b', B/'fast-v3', REPRO), ('fast-a', B/'fast-v3', FAST), ('fast-b', B/'fast-v3', FAST)]
LOCK = [sys.executable, str(Path(__file__).with_name('gpu_lock.py'))]


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(fixture, folder, lib, extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PPF_REPRO_', 'PPF_EXPERIMENTAL_HYDRO'))}
    env.update(extra); env['PATH'] = str(lib)+os.pathsep+str(frozen_bin)+os.pathsep+env['PATH']
    folder.mkdir(parents=True); loaded = None; peak = 0.; start = time.perf_counter()
    with (folder/'worker.log').open('w') as log:
        p = subprocess.Popen([str(worker), '--path', str(fixture), '--output', str(folder/'output')], env=env, stdout=log, stderr=subprocess.STDOUT)
        while p.poll() is None:
            if loaded is None: loaded = loaded_cuda_backend(p.pid)
            r = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], capture_output=True, text=True)
            peak = max([peak]+[float(s) for s in r.stdout.split() if s.strip().isdigit()]); time.sleep(0.5)
    elapsed = time.perf_counter()-start
    assert loaded and Path(loaded).resolve().parent == lib.resolve(), loaded
    text = (folder/'worker.log').read_text(errors='ignore')
    contacts = [int(m) for m in re.findall(r'\* num_contact: (\d+)', text)]
    stage = {}
    for name, ms in re.findall(r'> (\w+)\.\.\.(\d+) msec', text): stage[name] = stage.get(name, 0)+int(ms)
    return dict(exit_code=p.returncode, elapsed_seconds=elapsed, loaded_backend_sha256=sha(loaded),
                peak_whole_device_memory_mib=peak, newton_steps=len(contacts),
                contacts_mean=float(np.mean(contacts)) if contacts else 0., contacts_max=max(contacts, default=0),
                stage_ms={k: stage[k] for k in ('asm_contact', 'matrix_assembly', 'linsolve', 'line_search') if k in stage},
                outputs=len(list((folder/'output').glob('vert_*.bin'))),
                fallback='not converged' in text, failure='REPRO_FAST' in text and 'failure' in text)


def compare(a, b):
    names = sorted(q.name for q in (a/'output').glob('vert_*.bin'))
    differing = [n for n in names if not (b/'output'/n).is_file() or (a/'output'/n).read_bytes() != (b/'output'/n).read_bytes()]
    dev = 0.
    for n in names:
        if (b/'output'/n).is_file():
            x = np.fromfile(a/'output'/n, '<f4').astype(float); y = np.fromfile(b/'output'/n, '<f4').astype(float)
            if x.shape == y.shape: dev = max(dev, float(np.max(np.abs(x-y))))
    return dict(files=len(names), differing=len(differing), max_abs_deviation=dev)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenes', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--names', nargs='+', required=True)
    p.add_argument('--cases', nargs='+', help='rerun only these cases; earlier folders are kept as <case>-superseded-N')
    p.add_argument('--fast-lib', type=Path, help='backend directory for the fast cases (default fast-v3)')
    a = p.parse_args()
    cases = [(c, (a.fast_lib if a.fast_lib and c.startswith('fast') else lib), e) for c, lib, e in CASES
             if not a.cases or c in a.cases]
    a.output.mkdir(parents=True, exist_ok=True); path = a.output/'results.json'
    results = json.loads(path.read_text()) if path.exists() else {}
    for name in a.names:
        subprocess.run(LOCK+['acquire', f'contact benchmark {name}'], check=True)
        try:
            entry = dict(results.get(name, {}))
            for case, lib, extra in cases:
                old = a.output/name/case
                if old.exists():
                    k = 1
                    while (a.output/name/f'{case}-superseded-{k}').exists(): k += 1
                    old.rename(a.output/name/f'{case}-superseded-{k}')
                entry[case] = run(a.scenes/name, a.output/name/case, lib, extra)
                print(name, case, {k: entry[case][k] for k in ('exit_code', 'elapsed_seconds', 'contacts_mean', 'contacts_max', 'outputs')}, flush=True)
        finally:
            subprocess.run(LOCK+['release'])
        f = a.output/name
        entry['fast_vs_reference'] = compare(f/'fast-a', f/'reference')
        entry['fast_vs_fast'] = compare(f/'fast-b', f/'fast-a')
        entry['unordered_vs_reference'] = compare(f/'unordered', f/'reference')
        entry['reference_vs_reference'] = compare(f/'reference-b', f/'reference')
        results[name] = entry; path.write_text(json.dumps(results, indent=2))
        print(name, {k: entry[k] for k in ('fast_vs_reference', 'fast_vs_fast', 'reference_vs_reference', 'unordered_vs_reference')}, flush=True)


if __name__ == '__main__':
    main()
