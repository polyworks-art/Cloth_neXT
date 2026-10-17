"""Byte-compare a corrected Blender Bake session with the corrected native replays.

Inputs: physical solver inputs (bin/*.bin, param.toml) against the native fixture
manifest; outputs (vert_*.bin, hydro_*.bin) against each native replay run.
Run from the repo root.
"""
import argparse, hashlib, json
from pathlib import Path
import numpy as np

root = Path.cwd(); B = root/'.hydro-test-tmp/reproducibility-20261017'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--bake', type=Path, required=True)
    a = p.parse_args()
    report = json.loads((a.bake/'report.json').read_text())
    output = Path(report['native_output']); session = output.parent
    native = json.loads((B/'full-corrected-cached-five/manifest.json').read_text())
    inputs = {}
    for name, expected in native['inputs'].items():
        candidate = session/name.replace('\\', '/')
        inputs[name] = 'missing' if not candidate.is_file() else ('match' if sha(candidate) == expected else 'differs')
    extra = sorted(str(q.relative_to(session)) for q in session.rglob('*') if q.is_file() and
                   (q.suffix == '.bin' or q.name == 'param.toml') and 'output' not in q.parts and
                   str(q.relative_to(session)) not in native['inputs'])
    produced = sorted(q.name for q in output.iterdir() if q.name.startswith(('vert_', 'hydro_')) and q.suffix == '.bin')
    runs = []
    for run in range(len(native['runs'])):
        reference = B/f'full-corrected-cached-five/{run}/output'
        names = sorted(q.name for q in reference.iterdir() if q.name.startswith(('vert_', 'hydro_')) and q.suffix == '.bin')
        differing = [n for n in names if not (output/n).is_file() or (output/n).read_bytes() != (reference/n).read_bytes()]
        maximum = 0.
        for n in differing:
            if n.startswith('vert_') and (output/n).is_file():
                maximum = max(maximum, float(np.max(np.abs(np.fromfile(output/n, '<f4').astype(np.float64)
                                                           - np.fromfile(reference/n, '<f4')))))
        runs.append(dict(run=run, compared=len(names), differing=differing, max_native_abs_deviation=maximum,
                         same_file_set=names == produced))
    result = dict(scope='Blender Bake native session vs corrected native replays; byte equality',
                  session=str(session), inputs_match=all(v == 'match' for v in inputs.values()),
                  input_status={k: v for k, v in inputs.items() if v != 'match'}, inputs_not_in_fixture=extra,
                  produced=len(produced), runs=runs,
                  all_outputs_bit_identical=all(not r['differing'] and r['same_file_set'] for r in runs))
    (a.bake.parent/'blender-vs-native.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'runs'}, indent=2))
    print([(r['run'], r['compared'], len(r['differing']), r['max_native_abs_deviation']) for r in runs])


if __name__ == '__main__':
    main()
