"""Sequential, same-fixture native runtime/VRAM comparison (no other GPU work).

Cases on the unchanged NEON 130-159 fixture with original fluid transport:
  frozen     frozen 2.9.26 worker + frozen CUDA backend (unordered original)
  unordered  research worker + corrected cached backend, PPF_REPRO_* unset
  ordered    research worker + corrected cached backend, five PPF_REPRO_* opt-ins
Run from the repo root. Whole-device memory only; not per-process VRAM.
"""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from loaded_backend import loaded_cuda_backend

root = Path.cwd(); B = root/'.hydro-test-tmp/reproducibility-20261017'
frozen = root/'.hydro-test-tmp/blender-e2e-20261016/runtime'
fixture = B/'neon-fixture/session'
fluid = root/'.owner-test-tmp/20261017/published-2926-full-host/session-evidence/hydrodynamic-aa366355bde442c3b8f0c2412edd3447'
REPRO = dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all', PPF_REPRO_FILTERED_CONTACT='1',
             PPF_REPRO_SERIAL_CSR='1', PPF_REPRO_SERIAL_STITCH='1')
CASES = dict(frozen=(frozen/'target/cuda/release/ppf-contact-solver.exe', frozen/'bin', False),
             unordered=(B/'native-source/target/research-cuda/release/ppf-contact-solver.exe', B/'cached-cuda-build/lib', False),
             ordered=(B/'native-source/target/research-cuda/release/ppf-contact-solver.exe', B/'cached-cuda-build/lib', True))
# Timing-only ablations: exactly one ordering component enabled (not determinism controls).
ONLY = dict(only_colored=dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all'),
            only_contact=dict(PPF_REPRO_FILTERED_CONTACT='1'), only_csr=dict(PPF_REPRO_SERIAL_CSR='1'),
            only_stitch=dict(PPF_REPRO_SERIAL_STITCH='1'))
for _name in ONLY:
    CASES[_name] = (CASES['ordered'][0], CASES['ordered'][1], _name)


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--cases', nargs='+', default=list(CASES)); a = p.parse_args()
    a.output.mkdir(parents=True, exist_ok=False); results = []
    for name in a.cases:
        exe, lib, ordered = CASES[name]
        env = {k: v for k, v in os.environ.items() if not k.startswith('PPF_REPRO_')}
        env.update(PPF_EXPERIMENTAL_HYDRO_TEST_JSON=str(fluid/'model.json'), PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR=str(fluid/'volumes'))
        env['PATH'] = str(lib)+os.pathsep+str(frozen/'bin')+os.pathsep+env['PATH']
        if ordered is True: env.update(REPRO)
        elif ordered: env.update(ONLY[ordered])
        case = a.output/name; case.mkdir(); samples = []; loaded = None; start = time.perf_counter()
        with (case/'worker.log').open('w') as log:
            run = subprocess.Popen([str(exe), '--path', str(fixture), '--output', str(case/'output')], env=env, stdout=log, stderr=subprocess.STDOUT)
            while run.poll() is None:
                if loaded is None: loaded = loaded_cuda_backend(run.pid)
                r = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True)
                samples.append(dict(seconds=time.perf_counter()-start, sample=r.stdout.strip())); time.sleep(1)
        elapsed = time.perf_counter()-start
        files = sorted((case/'output').glob('vert_*.bin'))
        mem = [float(s['sample'].split(',')[0]) for s in samples if s['sample']]
        results.append(dict(case=name, worker_sha256=sha(exe), loaded_backend=loaded, loaded_backend_sha256=sha(loaded) if loaded else None,
                            ordered=ordered, exit_code=run.returncode, elapsed_seconds=elapsed, vertex_outputs=len(files),
                            finite=all(np.isfinite(np.fromfile(q, '<f4')).all() for q in files),
                            peak_whole_device_memory_mib=max(mem, default=None), samples=samples))
        print({k: v for k, v in results[-1].items() if k != 'samples'}, flush=True)
        (a.output/'results.json').write_text(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
