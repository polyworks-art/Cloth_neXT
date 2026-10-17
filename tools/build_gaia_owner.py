# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an unpublished owner ZIP and separate local runtime configuration.

No downloads, registry edits, version changes or publication. Runtime files,
surfaces and proprietary scenes never enter the extension staging directory.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cloth_next.gaia.container import GaiaContainer
from cloth_next.gaia.owner_test import digest, frame_identity, binding_digest
from tools.stage_companion import stage
from tools.build_extension import build_extension


def build(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # No symlinks or unknown content from arbitrary extension staging roots.
    source = ROOT / 'cloth_next'
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Extension source contains symlinks; inspect before packaging')
    extension = output / 'extension' / 'cloth_next'
    shutil.copytree(source, extension, ignore=shutil.ignore_patterns(
        '__pycache__', '*.pyc', 'bin', 'companion_manifest.json', '.state'))
    local = output / 'local-runtime'
    local.mkdir()
    surfaces = local / 'surfaces'
    surfaces.mkdir()
    bounds = local / 'bounds'
    bounds.mkdir()
    original = json.loads(args.surfaces.read_text(encoding='utf-8'))
    if original['release_approved'] is not False or original['usage'] != 'ISOLATED_BLENDER_VALIDATION_ONLY':
        raise ValueError('Requires an existing frozen reconstruction validation manifest')
    surface_root = Path(original['surface_directory'])
    dataset = dict(kind='ClothNeXt.OwnerNEONDataset.v1', release_approved=False,
                   surfaces='surfaces', bounds='bounds', surface_sha256={},
                   bounds_sha256={}, fluid_frames={}, validated_scenes=[])
    with GaiaContainer(args.cache) as container:
        bounds_root = Path(container.manifest['water']['provider']['cache'])
        for record in container.manifest['frames']:
            number = record['frame']
            if 130 <= number <= 160:
                dataset['fluid_frames'][str(number)] = frame_identity(record)
    if len(dataset['fluid_frames']) != 31:
        raise ValueError('Reference cache must contain every frame 130–160')
    for number in range(130, 161):
        name = f'{number:06}.bobj'
        if digest(surface_root / name) != original['files'][name]:
            raise ValueError(f'Frozen surface changed: {name}')
        shutil.copy2(surface_root / name, surfaces / name)
        dataset['surface_sha256'][name] = original['files'][name]
        name = f'bounds{number:06}.bbox'
        shutil.copy2(bounds_root / name, bounds / name)
        dataset['bounds_sha256'][name] = digest(bounds / name)
    for run in args.validated_run:
        report = json.loads((run / 'report.json').read_text(encoding='utf-8'))
        if report['result'] != 'PASS' or report.get('dry'):
            raise ValueError('Only successful hydrodynamic Blender runs can bind owner scenes')
        bindings = list((run / 'cache/.cloth_next_export/scene').glob('*/*/bind.json'))
        if not bindings and (run / 'session-evidence/official-scene-bindings.json').is_file():
            bindings = [run / 'session-evidence/official-scene-bindings.json']
        if len(bindings) != 1:
            raise ValueError('Reference run must have exactly one exported scene binding')
        bind = json.loads(bindings[0].read_text(encoding='utf-8'))
        dataset['validated_scenes'].append(dict(first=report['range'][0], last=report['range'][1],
            data_hash=bind['source_data_hash'], param_hash=bind['source_param_hash'],
            binding_sha256=binding_digest(bind)))
    dataset_path = local / 'neon-dataset.json'
    dataset_path.write_text(json.dumps(dataset, indent=2), encoding='utf-8')
    config = json.loads(args.runtime_manifest.read_text(encoding='utf-8'))
    if config['kind'] != 'ClothNeXt.IsolatedHydrodynamics.v1':
        raise ValueError('Requires the already verified isolated native runtime')
    # Leave the proven runtime in its separate existing directory. No copy over
    # official binaries, no global solver registry/selection changes.
    for role in ('server', 'worker', 'backend'):
        if digest(config[role]) != config[role + '_sha256']:
            raise ValueError(f'Experimental runtime identity changed: {role}')
    config.update(kind='ClothNeXt.OwnerHydrodynamics.v1', dataset='neon-dataset.json')
    config.pop('preparer', None)
    (local / 'owner-runtime.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    (extension / 'gaia_owner_test.json').write_text(json.dumps(dict(
        kind='ClothNeXt.OwnerBuild.v1', dataset_sha256=digest(dataset_path),
        production_ready=False, distribution='UNPUBLISHED_LOCAL_OWNER_EVALUATION')), encoding='utf-8')
    stage(args.companion, extension)
    archive = build_extension(extension, output / 'cloth-next-owner-local.zip', blender=str(args.blender))
    files = {str(path.relative_to(extension)).replace('\\', '/'): digest(path)
             for path in sorted(extension.rglob('*')) if path.is_file()}
    provenance = dict(publication=False, manifest_version_unchanged=True,
        source_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        source_is_working_snapshot=True, staged_files=files, archive_sha256=digest(archive),
        runtime_manifest_sha256=digest(local / 'owner-runtime.json'))
    (output / 'build-provenance.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    print(archive)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'surfaces', 'cache', 'runtime-manifest', 'companion', 'blender'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--validated-run', type=Path, action='append', required=True)
    build(parser.parse_args())


if __name__ == '__main__':
    main()
