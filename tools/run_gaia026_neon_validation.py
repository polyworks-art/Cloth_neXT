"""Serialize the completed NEON source behind the existing production matrix."""
import argparse
import atexit
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--solver', type=Path, required=True)
parser.add_argument('--wait-pid',type=int)
parser.add_argument('--resume-pid',type=int,nargs='+')
parser.add_argument('--matrix-name',default='neon-matrix')
parser.add_argument('--start-now',action='store_true')
args = parser.parse_args()
if args.resume_pid:
    import psutil
    def resume_owned_queue():
        for pid in args.resume_pid:
            try:
                process=psutil.Process(pid)
                if any('run_gaia026_production_remaining.py' in arg or
                       'run_gaia026_production_scale.py' in arg or
                       'blender_gaia026_production_scale.py' in arg or
                       'ppf-cts-server.exe' in arg for arg in process.cmdline()):
                    process.resume()
            except psutil.NoSuchProcess:
                pass
    atexit.register(resume_owned_queue)
deadline = time.monotonic() + 57600
def waiting():
    if args.start_now:return False
    if args.wait_pid:
        import psutil
        return psutil.pid_exists(args.wait_pid)
    return not (args.root / 'remaining-status.json').is_file()
while waiting():
    if time.monotonic() > deadline:
        raise TimeoutError('Existing serialized production tests did not finish')
    time.sleep(5)
ready = json.loads((args.root / 'neon-ready-final.json').read_text())
assert ready['result'] == 'READABLE_SNAPSHOT', ready
assert ready['source_blend_unchanged']
assert any(d.get('provider_accepts_full_range') for d in ready['domains'])
report = dict(result='RUNNING', source=ready['source'], gates=[])


def save():
    (args.root / 'neon-validation-report.json').write_text(json.dumps(report, indent=2))


def run(name, argv):
    started = time.monotonic()
    with (args.root / (name + '.log')).open('wb') as log:
        result = subprocess.run(list(map(str, argv)), cwd=ROOT, stdout=log,
            stderr=subprocess.STDOUT, timeout=21600, creationflags=subprocess.CREATE_NO_WINDOW)
    report['gates'].append(dict(name=name, returncode=result.returncode,
        seconds=time.monotonic() - started))
    save()
    print(name, result.returncode, flush=True)


save()
run('neon-native-matrix', [sys.executable, ROOT / 'tools/run_gaia026_production_scale.py',
    '--solver', args.solver, '--source', ready['source'], '--output', args.root / args.matrix_name,
    '--sizes', '1000', '--frames', '3', '60', '250', '--geometry-only', '--existing-scene',
    '--exclude-collider', 'SW_04_body_lod0_mesh.001',
    '--field-resolution', '128', '--time-step', '.001', '--cg-max-iter', '10000', '--cg-tol', '.001'])
matrix = json.loads((args.root / args.matrix_name / 'report.json').read_text())
report['native_cases'] = matrix['cases']
report['unexecuted'] = matrix['unexecuted']
if any(c['frames'] == 60 and c['result'] == 'PASS' for c in matrix['cases']):
    blender = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe')
    run('neon-dry-60', [blender, '--background', '--factory-startup', '--enable-autoexec',
        '--python-exit-code', '1', '--python', ROOT / 'tools/blender_gaia026_production_scale.py',
        '--', '--repo', ROOT, '--solver', args.solver, '--fluid-source', ready['source'],
        '--output', args.root / 'neon-dry-60', '--vertices', '1000', '--last', '60', '--dry', '--existing-scene',
        '--exclude-collider', 'SW_04_body_lod0_mesh.001',
        '--timeout', '1800', '--time-step', '.001', '--cg-max-iter', '10000', '--cg-tol', '.001'])
report['result'] = 'PASS' if (matrix['result'] == 'PASS'
    and all(g['returncode'] == 0 for g in report['gates'])) else 'FAIL'
save()
