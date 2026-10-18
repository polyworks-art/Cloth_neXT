"""Fast deterministic contact vs the frozen deterministic reference (native, NEON).

Each case: worker + backend directory + extra environment on the same fixture.
Compares every vert_*/hydro_* output file byte-for-byte with the first case and
reports runtime. Run from the repo root.
"""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from loaded_backend import loaded_cuda_backend

root = Path.cwd(); B = root/'.hydro-test-tmp/reproducibility-20261017'
frozen_bin = root/'.hydro-test-tmp/blender-e2e-20261016/runtime/bin'
fluid = root/'.owner-test-tmp/20261017/published-2926-full-host/session-evidence/hydrodynamic-aa366355bde442c3b8f0c2412edd3447'
# The exe directory must hold NO backend DLL: Windows searches it before PATH.
worker = Path(os.environ.get('PPF_BENCH_WORKER', B/'native-source/target/research-cuda/release/ppf-contact-solver.exe'))
assert not (worker.parent/'libsimbackend_cuda.dll').exists()
REPRO = dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all', PPF_REPRO_FILTERED_CONTACT='1',
             PPF_REPRO_SERIAL_CSR='1', PPF_REPRO_SERIAL_STITCH='1')
CASES = dict(reference=(B/'reference-deterministic', {}),
             fast=(B/'fast-cuda-build/lib', dict(PPF_REPRO_FAST_CONTACT='1')),
             fast_hook_only=(B/'fast-cuda-build/lib', {}),
             fast_all=(B/'fast-cuda-build/lib', dict(PPF_REPRO_FAST_CONTACT='1', PPF_REPRO_FAST_SERIAL='1')),
             fast_v3=(B/'fast-v3', dict(PPF_REPRO_SERIAL_DIRICHLET='1', PPF_REPRO_FAST_CONTACT='1', PPF_REPRO_FAST_SERIAL='1')),
             fast_v4=(B/'fast-v4', dict(PPF_REPRO_SERIAL_DIRICHLET='1', PPF_REPRO_FAST_CONTACT='1', PPF_REPRO_FAST_SERIAL='1')),
             fast_v5=(B/'fast-v4', dict(PPF_REPRO_SERIAL_DIRICHLET='1', PPF_REPRO_FAST_CONTACT='1', PPF_REPRO_FAST_SERIAL='1', PPF_REPRO_WALK_SPMV='1')))


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cases', nargs='+', default=['reference', 'fast']); p.add_argument('--repeat', type=int, default=1)
    a = p.parse_args(); a.output.mkdir(parents=True, exist_ok=False); results = []
    runs = [(c, r) for r in range(a.repeat) for c in a.cases]
    for name, rep in runs:
        lib, extra = CASES[name]
        env = {k: v for k, v in os.environ.items() if not k.startswith('PPF_REPRO_')}
        env.update(REPRO, **extra, PPF_EXPERIMENTAL_HYDRO_TEST_JSON=str(fluid/'model.json'),
                   PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=str(fluid/'volumes'))
        env['PATH'] = str(lib)+os.pathsep+str(frozen_bin)+os.pathsep+env['PATH']
        case = a.output/f'{name}-{rep}'; case.mkdir(); loaded = None; start = time.perf_counter(); peak = 0.
        with (case/'worker.log').open('w') as log:
            run = subprocess.Popen([str(worker), '--path', str(a.fixture), '--output', str(case/'output')], env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
            while run.poll() is None:
                if loaded is None: loaded = loaded_cuda_backend(run.pid)
                r = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], capture_output=True, text=True)
                peak = max([peak]+[float(s) for s in r.stdout.split() if s.strip().isdigit()]); time.sleep(1)
        assert loaded and Path(loaded).resolve().parent == lib.resolve(), (name, loaded)
        files = {q.name: q for q in (case/'output').iterdir() if q.name.startswith(('vert_', 'hydro_')) and q.suffix == '.bin'}
        results.append(dict(case=name, repeat=rep, exit_code=run.returncode, elapsed_seconds=time.perf_counter()-start,
                            loaded_backend=loaded, loaded_backend_sha256=sha(loaded) if loaded else None,
                            outputs=len(files), peak_whole_device_memory_mib=peak, folder=str(case)))
        base = Path(results[0]['folder'])/'output'
        diff = [n for n in sorted(files) if not (base/n).is_file() or (base/n).read_bytes() != files[n].read_bytes()]
        dev = 0.
        for n in diff:
            if n.startswith('vert_') and (base/n).is_file():
                dev = max(dev, float(np.max(np.abs(np.fromfile(files[n], '<f4').astype(float)-np.fromfile(base/n, '<f4')))))
        results[-1].update(differing_vs_first=diff, max_abs_deviation_vs_first_m=dev,
                           same_file_set=sorted(files) == sorted(q.name for q in base.iterdir() if q.name.startswith(('vert_', 'hydro_')) and q.suffix == '.bin'))
        print({k: (v if k != 'differing_vs_first' else len(v)) for k, v in results[-1].items() if k != 'folder'}, flush=True)
        (a.output/'results.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
