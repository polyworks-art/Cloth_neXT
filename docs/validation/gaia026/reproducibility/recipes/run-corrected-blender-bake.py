"""Corrected-solver regular Blender Bake in a brand-new isolated runtime/profile.

Copies (never modifies) the frozen runtime, frozen 2.9.26 owner package and the
frozen NEON blend copy; swaps only the research worker and the cached corrected
CUDA backend into the COPIED runtime; enables the five PPF_REPRO_* ordering
opt-ins; runs tools/run_gaia_owner_install.py (fresh install + actual
bpy.ops.clothnext.bake). Records loaded-DLL identity of every live worker and
whole-device GPU memory, and source integrity before/after. Run from the repo root.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loaded_backend import loaded_cuda_backend

root = Path.cwd()
B = root/'.hydro-test-tmp/reproducibility-20261017'
FROZEN_RUNTIME = root/'.hydro-test-tmp/blender-e2e-20261016/runtime'
FROZEN_BLEND = root/'.hydro-test-tmp/blender-e2e-20261016/SW04_NEON-copy.blend'
REFERENCE_PROFILE = root/'.hydro-test-tmp/blender-e2e-20261016/profile'
FROZEN_BUILD = root/'.owner-test-tmp/20261017/published-2926'
DATASET = root/'.owner-test-tmp/20261017/build-r6/local-runtime/neon-dataset.json'
DEPENDENCIES = root.parent/'ppf-contact-solver-hydrodynamics/.tools/python-deps'
ORIGINAL_BLEND = Path('F:/16_Cloth_neXt/SW04/SW04_NEON.blend')
FLIP_CACHE = Path('F:/16_Cloth_neXt/SW04/SW04_NEON_flip_fluid_cache')
WORKER = B/'native-source/target/research-cuda/release/ppf-contact-solver.exe'
BACKEND = B/'cached-cuda-build/lib/libsimbackend_cuda.dll'
EXPECTED = dict(owner_zip='382b14bb2b37f3a3bd7e576c051db535e6180d6f9e3d372c8d2a28fc447176c0',
                worker='f3a071af94155fa2378967ff44a8c3bf17c7b9809327cde5783d180a9a831b38',
                backend='9f4f156c4116a8e1a0c516889648a60d8a68901daf964c5dd6d54f9f1953712b',
                frozen_blend='d0a7055fe124b807308308b78ad85f2a098ece209566e03d3c17f1d4fbaa2437',
                original_blend='c22a2da5e37137d9db24be80a38074bbfe1290b8dc190c8f026df695715c0af3')
REPRO = dict(PPF_REPRO_ORDER_MODE='colored', PPF_REPRO_ORDER_FILTER='all', PPF_REPRO_FILTERED_CONTACT='1',
             PPF_REPRO_SERIAL_CSR='1', PPF_REPRO_SERIAL_STITCH='1')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def integrity():
    files = json.loads((root/'.owner-test-tmp/20261017/original-source-integrity.json').read_text())['files']
    index = {p.name: p for p in FLIP_CACHE.rglob('*') if p.is_file() and p.name in {f['file'] for f in files}}
    flip = [dict(file=f['file'], ok=f['file'] in index and sha(index[f['file']]) == f['sha256']) for f in files]
    frozen = {str(p.relative_to(FROZEN_RUNTIME)): sha(p) for p in sorted(FROZEN_RUNTIME.rglob('*'))
              if p.is_file() and p.suffix.lower() in ('.exe', '.dll')}
    # The owner's own GUI Blender may legitimately save the original; record its
    # identity before/after instead of asserting the historic hash.
    return dict(original_blend_sha256=sha(ORIGINAL_BLEND),
                frozen_blend=sha(FROZEN_BLEND) == EXPECTED['frozen_blend'],
                frozen_owner_zip=sha(FROZEN_BUILD/'cloth-next-owner-local.zip') == EXPECTED['owner_zip'],
                flip_files_checked=len(flip), flip_all_match=all(f['ok'] for f in flip), frozen_runtime_binaries=frozen)


def worker_pids():
    out = subprocess.run(['powershell', '-NoProfile', '-Command',
        "Get-Process ppf-contact-solver -ErrorAction SilentlyContinue | ForEach-Object { \"$($_.Id)|$($_.Path)\" }"],
        capture_output=True, text=True).stdout
    return [(int(a), b) for a, b in (line.split('|', 1) for line in out.splitlines() if '|' in line)]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--blender', default='C:/Program Files (x86)/Steam/steamapps/common/Blender/blender.exe')
    p.add_argument('--first', type=int, default=130)
    p.add_argument('--last', type=int, default=159)
    a = p.parse_args()
    C = a.root.resolve(); C.mkdir(parents=True, exist_ok=False)
    evidence = dict(scope='Corrected-solver regular Blender Bake; not a release or production certification',
                    repro_environment=REPRO, before=integrity())
    assert all(evidence['before'][k] for k in ('frozen_blend', 'frozen_owner_zip', 'flip_all_match')), evidence
    evidence['original_blend_matches_frozen_record'] = evidence['before']['original_blend_sha256'] == EXPECTED['original_blend']
    assert sha(WORKER) == EXPECTED['worker'] and sha(BACKEND) == EXPECTED['backend']
    runtime = C/'runtime'
    # Do not carry over earlier runs' mutable server session state/symlinks
    # (local/share/ppf-cts/<git>/...); the server recreates it. The python
    # junction is copied as a real directory for full isolation.
    def ignore(directory, names):
        return names if Path(directory).parent.name == 'ppf-cts' and Path(directory).parent.parent.name == 'share' else []
    shutil.copytree(FROZEN_RUNTIME, runtime, ignore=ignore)
    shutil.copy2(WORKER, runtime/'target/cuda/release/ppf-contact-solver.exe')
    shutil.copy2(BACKEND, runtime/'bin/libsimbackend_cuda.dll')
    build = C/'build'; (build/'local-runtime').mkdir(parents=True)
    shutil.copy2(FROZEN_BUILD/'cloth-next-owner-local.zip', build/'cloth-next-owner-local.zip')
    assert sha(build/'cloth-next-owner-local.zip') == EXPECTED['owner_zip']
    server = runtime/'target/cuda/release/ppf-cts-server.exe'
    worker = runtime/'target/cuda/release/ppf-contact-solver.exe'
    backend = runtime/'bin/libsimbackend_cuda.dll'
    manifest = dict(kind='ClothNeXt.OwnerHydrodynamics.v1', runtime=str(runtime), server=str(server),
                    worker=str(worker), backend=str(backend), python=str(runtime/'python/python.exe'),
                    dependencies=str(DEPENDENCIES), server_sha256=sha(server), worker_sha256=sha(worker),
                    backend_sha256=sha(backend), dataset=str(DATASET))
    assert manifest['worker_sha256'] == EXPECTED['worker'] and manifest['backend_sha256'] == EXPECTED['backend']
    (build/'local-runtime/owner-runtime.json').write_text(json.dumps(manifest, indent=2))
    blend = C/'SW04_NEON-copy.blend'; shutil.copy2(FROZEN_BLEND, blend)
    assert sha(blend) == EXPECTED['frozen_blend']
    evidence['owner_manifest'] = manifest
    # The add-on refuses a selected solver other than the manifest's server, so the
    # copied registry must select the NEW runtime. Reference profile stays untouched.
    reference = C/'reference-profile'
    for directory in ('superhivemarket_com/flip_fluids_addon', 'api_portal_polyhammer_com/character_dna',
                      'api_portal_polyhammer_com/character_assembly'):
        shutil.copytree(REFERENCE_PROFILE/'extensions'/directory, reference/'extensions'/directory)
    registry_path = Path('localappdata/ClothNeXt/solver/registry.json')
    registry = json.loads((REFERENCE_PROFILE/registry_path).read_text())
    for item in registry['installations']:
        for key in ('root_path', 'executable_path', 'frontend_path'):
            if item.get(key):
                item[key] = str(runtime/Path(item[key]).relative_to(FROZEN_RUNTIME))
    assert registry['selected_installation_id'] and Path(registry['installations'][0]['executable_path']) == server
    (reference/registry_path).parent.mkdir(parents=True)
    (reference/registry_path).write_text(json.dumps(registry, indent=2))
    evidence['registry'] = registry
    env = {k: v for k, v in os.environ.items() if not k.startswith('PPF_REPRO_')}
    env.update(REPRO)
    workers, memory, stop = {}, [], threading.Event()

    def monitor():
        start = time.perf_counter()
        while not stop.is_set():
            for pid, path in worker_pids():
                if pid not in workers or workers[pid]['loaded_backend'] is None:
                    loaded = loaded_cuda_backend(pid)
                    workers[pid] = dict(pid=pid, path=path, loaded_backend=loaded,
                                        loaded_backend_sha256=sha(loaded) if loaded else None,
                                        first_seen_s=workers.get(pid, {}).get('first_seen_s', time.perf_counter()-start))
            r = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                               capture_output=True, text=True)
            memory.append(dict(seconds=time.perf_counter()-start,
                               mib=[float(s) for s in r.stdout.split() if s.strip().isdigit()]))
            stop.wait(1)

    thread = threading.Thread(target=monitor, daemon=True); thread.start()
    started = time.perf_counter()
    cmd = [sys.executable, str(root/'tools/run_gaia_owner_install.py'), '--build', str(build), '--profile', str(C/'profile'),
           '--reference-profile', str(reference), '--blend', str(blend), '--blender', a.blender,
           '--output', str(C/'bake'), '--first', str(a.first), '--last', str(a.last), '--flip-cache', str(FLIP_CACHE)]
    with (C/'launcher.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, cwd=root)
    stop.set(); thread.join()
    evidence.update(launcher_exit=result.returncode, elapsed_s=time.perf_counter()-started, workers=list(workers.values()),
                    backend_identity_verified=bool(workers) and all(w['loaded_backend_sha256'] == EXPECTED['backend']
                                                                   and Path(w['loaded_backend']).resolve() == backend.resolve()
                                                                   for w in workers.values() if w['loaded_backend']),
                    peak_whole_device_memory_mib=max((max(m['mib'], default=0) for m in memory), default=None),
                    device_memory_scope='whole GPU, shared with other clients; not per-process VRAM',
                    after=integrity())
    (C/'memory.json').write_text(json.dumps(memory))
    evidence['integrity_unchanged'] = evidence['after'] == evidence['before']
    (C/'evidence.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps({k: evidence[k] for k in ('launcher_exit', 'elapsed_s', 'backend_identity_verified',
                                                'integrity_unchanged', 'peak_whole_device_memory_mib')}, indent=2))
    return 0 if result.returncode == 0 and evidence['backend_identity_verified'] and evidence['integrity_unchanged'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
