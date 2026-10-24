# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned preparation of typed experimental transport; no host cloth forces."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from ..ppf.hydrodynamic_capability import experimental_configuration
from ..core.errors import ClothNextError, ErrorCategory, ErrorRecord
from ..core.safe_delete import delete_owned

# Prepared FLIP surfaces are reused while the FLIP cache and every
# preparation input are unchanged. They are large (about 12 MB per frame),
# so only the most recently used entries are kept.
CACHE_ENV = 'CLOTH_NEXT_HYDRO_CACHE'
KEEP_ENTRIES = 2
_COMPLETE = 'complete.json'


def cache_root():
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override)
    from ..updater.install_paths import ManagedSolverPaths
    return ManagedSolverPaths.default().root.parent / 'hydro-cache'


def is_shared_volume(path):
    """True for volumes inside the reuse cache (never deleted by a session)."""
    try:
        Path(path).resolve().relative_to(cache_root().resolve())
        return True
    except (ValueError, OSError):
        return False


def _source_fingerprint(path):
    """Names, sizes and modification times of every file of the FLIP cache."""
    root = Path(path)
    rows = []
    for directory, folders, files in os.walk(root):
        folders.sort()
        for name in sorted(files):
            item = Path(directory) / name
            stat = item.stat()
            rows.append([item.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns])
    return rows


def preparation_key(descriptor, config, script):
    document = {
        'version': 1,
        'cache': str(Path(descriptor['path']).resolve()),
        'sources': _source_fingerprint(descriptor['path']),
        'first': descriptor['first'], 'last': descriptor['last'],
        'source_last': descriptor.get('source_last', descriptor['last'] + 1),
        'fps': descriptor['fps'], 'bounds': list(descriptor['bounds']),
        'surface_scale': 4,
        'preparer': hashlib.sha256(Path(script).read_bytes()).hexdigest(),
        'python': str(config['python']), 'dependencies': str(config.get('dependencies') or ''),
        'owner_dataset': str(config.get('dataset') or '') if config.get('owner_test') else '',
    }
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode('utf-8')).hexdigest()


def _complete_entry(entry, total):
    try:
        marker = json.loads((entry / _COMPLETE).read_text(encoding='utf-8'))
        manifest = json.loads((entry / 'volumes' / 'hydrodynamics.json').read_text())
        return (marker.get('frames') == total and manifest['frames'] == total
                and manifest['roles'] == ['velocity_m_s_support',
                                          'signed_distance_m_x_identity'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _remove(path, root):
    try:
        delete_owned(path, root=root, ownership_authenticated=True, recursive=True,
                     lifecycle_stage='HYDRO_CACHE', artifact_type='hydrodynamic_volumes')
    except OSError:
        pass


def _prune(root, keep):
    entries = []
    for entry in root.iterdir() if root.is_dir() else ():
        if entry.is_dir() and '.tmp-' in entry.name:
            # Unfinished preparation of an earlier run (cancelled or crashed).
            if time.time() - entry.stat().st_mtime > 3600:
                _remove(entry, root)
            continue
        marker = entry / _COMPLETE
        if marker.is_file():
            entries.append((marker.stat().st_mtime, entry))
    for _mtime, entry in sorted(entries, reverse=True)[keep:]:
        _remove(entry, root)


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
    model = root / 'model.json'
    model.write_text(json.dumps(descriptor['model'], allow_nan=False), encoding='utf-8')
    script = Path(config.get('preparer')
                  or Path(__file__).with_name('prepare_experimental.py')).resolve(strict=True)
    total = descriptor['last']-descriptor['first']+2
    shared, entry, cache = None, None, None
    try:
        cache = cache_root()
        key = preparation_key(descriptor, config, script)
        entry = cache / key
        if _complete_entry(entry, total):
            os.utime(entry / _COMPLETE)              # most recently used
            progress(total, total)
            return {'PPF_EXPERIMENTAL_HYDRO_TEST_JSON': str(model.resolve()),
                    'PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR': str((entry / 'volumes').resolve())}
        cache.mkdir(parents=True, exist_ok=True)
        shared = cache / (key + '.tmp-' + uuid.uuid4().hex)
        shared.mkdir()
    except OSError:
        shared = None                                # no cache: prepare in the work folder
    staging = shared if shared is not None else root
    volume = staging / 'volumes'
    try:
        _run_preparer(config, script, descriptor, volume, staging, total,
                      check_cancel=check_cancel, progress=progress)
    except BaseException:
        if shared is not None:
            _remove(shared, cache)
        raise
    (staging/'request.json').write_text(json.dumps(descriptor,indent=2),encoding='utf-8')
    if shared is not None:
        (shared / _COMPLETE).write_text(json.dumps({'frames': total}), encoding='utf-8')
        try:
            os.replace(shared, entry)
        except OSError:
            # Another Blender prepared the same entry meanwhile; use theirs.
            _remove(shared, cache)
            if not _complete_entry(entry, total):
                raise
        volume = entry / 'volumes'
        _prune(cache, KEEP_ENTRIES)
    progress(total,total)
    return {'PPF_EXPERIMENTAL_HYDRO_TEST_JSON':str(model.resolve()),
            'PPF_EXPERIMENTAL_HYDRO_VOLUME_DIR':str(volume.resolve())}


def _run_preparer(config, script, descriptor, volume, root, total, *, check_cancel, progress):
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
