"""Fresh local extension-manager installation, then installed-package Blender bake."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('build', 'profile', 'reference-profile', 'blend', 'blender', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--first', type=int, default=130)
    parser.add_argument('--last', type=int, default=132)
    parser.add_argument('--dry', action='store_true')
    parser.add_argument('--reuse-profile', action='store_true',
                        help='Reuse an already installed owner test profile; do not install or change its registry')
    parser.add_argument('--backend', choices=('CUDA', 'CPU'), default='CUDA')
    parser.add_argument('--cancel-at-stage', choices=('PREPARING_WATER', 'EXPORTING', 'SIMULATING'))
    parser.add_argument('--modify-material', action='store_true')
    parser.add_argument('--expect-error')
    parser.add_argument('--flip-cache', type=Path)
    a = parser.parse_args()
    for name in ('build', 'profile', 'reference_profile', 'blend', 'blender', 'output'):
        setattr(a, name, getattr(a, name).resolve())
    if a.reuse_profile:
        if not (a.profile / 'extensions/gaia_owner/cloth_next/gaia_owner_test.json').is_file():
            raise ValueError('Not an installed local owner test profile')
    else:
        a.profile.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    for key in ('CLOTH_NEXT_EXPERIMENTAL_HYDRO_MANIFEST', 'CARGO_TARGET_DIR', 'PYTHONPATH'):
        env.pop(key, None)
    env.update(BLENDER_USER_RESOURCES=str(a.profile), LOCALAPPDATA=str(a.profile / 'localappdata'),
               PYTHONDONTWRITEBYTECODE='1')
    temporary = a.profile / 'temp'
    temporary.mkdir(exist_ok=a.reuse_profile)
    env.update(TEMP=str(temporary), TMP=str(temporary))
    for directory in ('superhivemarket_com/flip_fluids_addon',
                      'api_portal_polyhammer_com/character_dna',
                      'api_portal_polyhammer_com/character_assembly'):
        if not a.reuse_profile:
            shutil.copytree(a.reference_profile / 'extensions' / directory,
                            a.profile / 'extensions' / directory)
    registry = Path('localappdata/ClothNeXt/solver/registry.json')
    if not a.reuse_profile:
        (a.profile / registry).parent.mkdir(parents=True)
        shutil.copy2(a.reference_profile / registry, a.profile / registry)
    commands = [
        ['--command', 'extension', 'repo-add', 'gaia_owner', '--name', 'GAIA Local Owner',
         '--directory', str(a.profile / 'extensions/gaia_owner')],
        ['--command', 'extension', 'install-file', '-r', 'gaia_owner', '-e',
         str(a.build / 'cloth-next-owner-local.zip')]]
    for number, command in enumerate([] if a.reuse_profile else commands):
        with (a.profile / f'install-{number}.log').open('w', encoding='utf-8') as log:
            subprocess.run([str(a.blender), '--background', *command], env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
    root = Path(__file__).resolve().parents[1]
    command = [str(a.blender), '--background', '--python',
        str(root / 'tools/blender_neon_hydrodynamic_e2e.py'), '--', '--repo', str(root),
        '--blend', str(a.blend), '--output', str(a.output), '--first', str(a.first),
        '--last', str(a.last), '--addon-module', 'bl_ext.gaia_owner.cloth_next',
        '--owner-manifest', str(a.build / 'local-runtime/owner-runtime.json'),
        '--backend', a.backend]
    if a.dry:command.append('--dry')
    if a.cancel_at_stage:command += ['--cancel-at-stage', a.cancel_at_stage]
    if a.modify_material:command.append('--modify-material')
    if a.expect_error:command += ['--expect-error', a.expect_error]
    if a.flip_cache:command += ['--flip-cache', str(a.flip_cache.resolve())]
    with (a.profile / (a.output.name + '-bake.log')).open('x', encoding='utf-8') as log:
        result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
    report = json.loads((a.output / 'report.json').read_text(encoding='utf-8'))
    print(json.dumps({key: report.get(key) for key in ('result', 'state', 'error', 'traceback', 'seconds')}, indent=2))
    return 0 if result.returncode == 0 and report['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
