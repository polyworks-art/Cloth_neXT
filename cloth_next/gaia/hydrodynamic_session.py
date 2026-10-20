# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned preparation of typed experimental transport; no host cloth forces."""
import json
from pathlib import Path
import subprocess
import time
import uuid

from ..ppf.hydrodynamic_capability import experimental_configuration
from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord


def prepare_session(descriptor, executable, work, *, check_cancel, progress, scene=None):
    config = experimental_configuration(executable)
    if config.get('owner_test'):
        from .owner_test import validate_scene
        try:
            validate_scene(config, descriptor, scene)
        except ValueError as exc:
            raise ClothNextError(ErrorRecord.create(category=ErrorCategory.CACHE,
                user_message=str(exc), technical_message=str(exc),
                recommended_action='Open the frozen NEON owner scene with its original settings and choose a validated frame range, or disable Hydrodynamic Pressure for normal cloth simulation.',
                recoverable=True)) from exc
    root = Path(work) / ('hydrodynamic-' + uuid.uuid4().hex)
    root.mkdir()
    volume = root / 'volumes'
    model = root / 'model.json'
    model.write_text(json.dumps(descriptor['model'], allow_nan=False), encoding='utf-8')
    script = Path(config.get('preparer')
                  or Path(__file__).with_name('prepare_experimental.py')).resolve(strict=True)
    command = [config['python'], str(script)]
    if config.get('dependencies'):
        command += ['--dependencies', config['dependencies']]
    command += ['--output', str(volume), '--cache', descriptor['path'],
               '--first', str(descriptor['first']),
               '--frames', str(descriptor['last']-descriptor['first']+2),
               '--source-last', str(descriptor.get('source_last', descriptor['last']+1)),
               '--fps', str(descriptor['fps']), '--surface-scale', '4', '--bounds',
               *map(str, descriptor['bounds'])]
    if config.get('owner_test'):
        command += ['--owner-dataset', config['dataset']]
    total = descriptor['last']-descriptor['first']+2
    with (root / 'preparation.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        previous = -1
        try:
            while process.poll() is None:
                check_cancel()
                try:
                    count = len(json.loads((volume/'preparation-progress.json').read_text()))
                except (OSError, ValueError):count = 0
                if count != previous:
                    progress(count,total)
                    previous = count
                time.sleep(.1)
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=10)
        if process.returncode:
            tail=(root/'preparation.log').read_text(encoding='utf-8',errors='replace')[-6000:]
            raise ClothNextError(ErrorRecord.create(category=ErrorCategory.CACHE,
                user_message='Hydrodynamic FLIP surface preparation failed.',
                technical_message=tail,
                recommended_action='Inspect the reported source frame. Disconnected closed water parts are supported; open or non-manifold surfaces require a valid export.',
                recoverable=True))
    manifest=json.loads((volume/'hydrodynamics.json').read_text())
    if manifest['frames']!=total or manifest['roles']!=[
            'velocity_m_s_support','signed_distance_m_x_identity']:
        raise ValueError('Hydrodynamic transport is incomplete')
    (root/'request.json').write_text(json.dumps(descriptor,indent=2),encoding='utf-8')
    progress(total,total)
    return {'PPF_EXPERIMENTAL_HYDRO_TEST_JSON':str(model.resolve()),
            'PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR':str(volume.resolve())}
