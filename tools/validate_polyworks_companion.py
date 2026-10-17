"""Stage and test the new EXE in an isolated copy, preserving existing bundles."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.stage_companion import stage
from tools.verify_companion import verify_information_modes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--blender', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    target = out/'package'/'cloth_next'
    shutil.copytree(ROOT/'cloth_next', target,
        ignore=shutil.ignore_patterns('__pycache__','*.pyc','bin','companion_manifest.json','.state'), dirs_exist_ok=False)
    staged = stage(args.exe.resolve(), target)
    env = dict(os.environ, BLENDER_USER_RESOURCES=str(out/'profile'),
        LOCALAPPDATA=str(out/'appdata'), CLOTH_NEXT_COMPANION_AUTO_CLOSE_MS='3000',
        PYTHONDONTWRITEBYTECODE='1')
    with (out/'blender.log').open('w') as log:
        result = subprocess.run([str(args.blender), '--factory-startup', '--background', '--python',
            str(ROOT/'tools/blender_polyworks_account_smoke.py'), '--', str(target.parent),
            str(out/'blender.json')], env=env, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    assert result.returncode == 0, (out/'blender.log').read_text(errors='replace')
    # Existing Welcome/splash/What's-New actual EXE lifecycle checks.
    verify_information_modes(staged)
    report = dict(result='PASS', exe=str(staged), sha256=hashlib.sha256(staged.read_bytes()).hexdigest(),
        welcome_and_whats_new='PASS', blender=json.loads((out/'blender.json').read_text()),
        production_api_contacted=False, original_bundle_replaced=False)
    (out/'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
