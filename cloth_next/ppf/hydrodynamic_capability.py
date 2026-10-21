# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed gate for the native triangle-pressure extension.

Protocol 0.26 and native FluidVolume support do not certify triangle pressure.
A selected installation qualifies only when its hash-listed CUDA worker reports
the velocity/SDF transport and the native SDF bounds guard.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class HydrodynamicCapability:
    available: bool
    reason: str


def experimental_configuration(executable=None):
    """Opt in per isolated Blender process, binding server, worker and backend."""
    value = os.environ.get('CLOTH_NEXT_EXPERIMENTAL_HYDRO_MANIFEST', '')
    if not value:
        from ..gaia import owner_test
        config = (owner_test.configuration() if owner_test.enabled()
                  else installation_configuration(executable))
    else:
        path = Path(value).resolve(strict=True)
        profile = Path(os.environ.get('BLENDER_USER_RESOURCES', '')).resolve(strict=True)
        path.relative_to(profile)
        config = json.loads(path.read_text(encoding='utf-8'))
        if config.get('kind') != 'ClothNeXt.IsolatedHydrodynamics.v1':
            raise ValueError('Invalid isolated hydrodynamics configuration')
    server = Path(config['server']).resolve(strict=True)
    if executable is not None and Path(executable).resolve() != server:
        raise ValueError('Selected solver differs from the isolated hydrodynamics solver')
    identities = []
    for role in ('server', 'worker', 'backend'):
        candidate = Path(config[role]).resolve(strict=True)
        candidate.relative_to(Path(config['runtime']).resolve(strict=True))
        stat = candidate.stat()
        identities.append((str(candidate), stat.st_size, stat.st_mtime_ns, config[role+'_sha256']))
    _verify(tuple(identities))
    return config


def installation_configuration(executable):
    """Bind the pressure runtime to the selected installation's own files.

    Server, worker, backend and Python must match the installation's
    SHA256SUMS.txt, which the installer verified before registering it.
    """
    if executable is None:
        raise ValueError('Select a GAIA solver installation first')
    from ..updater.health_runner import bundle_root_for
    server = Path(executable).resolve(strict=True)
    root = bundle_root_for(server)
    files = {'server': server,
             'worker': server.with_name('ppf-contact-solver' + server.suffix),
             'backend': root / 'bin' / ('libsimbackend_cuda.dll' if os.name == 'nt'
                                        else 'libsimbackend_cuda.so'),
             'python': root / 'python' / ('python.exe' if os.name == 'nt' else 'bin/python3')}
    sums = root / 'SHA256SUMS.txt'
    if not sums.is_file():
        raise ValueError('Requires a verified GAIA installation with native pressure transport')
    listed = {}
    for line in sums.read_text(encoding='utf-8').splitlines():
        digest, _, name = line.strip().partition('  ')
        if name:
            listed[name.strip()] = digest.lower()
    config = {'runtime': str(root), 'dependencies': '', 'installation': True}
    for role, path in files.items():
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else None
        if not path.is_file() or relative not in listed:
            raise ValueError('Requires a verified GAIA CUDA installation with native pressure '
                             'transport (missing ' + path.name + ')')
        config[role] = str(path)
        config[role + '_sha256'] = listed[relative]
    site = files['python'].parent / 'Lib' / 'site-packages'
    if os.name == 'nt' and not all((site / name).is_dir() for name in ('numpy', 'scipy')):
        raise ValueError('The solver installation Python lacks NumPy/SciPy for FLIP surface preparation')
    return config


@lru_cache(maxsize=4)
def _verify(identities):
    for name, _size, _mtime, expected in identities:
        with Path(name).open('rb') as stream:
            observed = hashlib.file_digest(stream, 'sha256').hexdigest()
        if observed != expected:
            raise ValueError('Experimental solver binary identity changed: '+name)
    worker = identities[1][0]
    environment = os.environ.copy()
    environment['PATH'] = str(Path(identities[2][0]).parent) + os.pathsep + environment.get('PATH', '')
    result = subprocess.run([worker, '--probe'], capture_output=True, text=True,
                            env=environment,
                            timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    required = ('backend: cuda',
                'hydrodynamic-fluid-transport: experimental-velocity-sdf-bricks-v1',
                'hydrodynamic-sdf-bounds: fail-closed-v1')
    if result.returncode or any(line not in result.stdout.splitlines() for line in required):
        raise ValueError('Experimental CUDA worker lacks verified pressure transport or native SDF bounds protection')


def hydrodynamic_capability(executable=None):
    try:
        experimental_configuration(executable)
        return HydrodynamicCapability(True, 'Native CUDA pressure transport and SDF bounds guard verified')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return HydrodynamicCapability(False, str(exc))


def require_hydrodynamic_capability(executable=None):
    capability = hydrodynamic_capability(executable)
    if not capability.available:
        raise ValueError('Hydrodynamic Pressure unavailable: '+capability.reason)


WET_FABRIC_PROBE = 'hydrodynamic-wet-fabric: buoyancy-absorption-v1'


@lru_cache(maxsize=8)
def _wet_fabric_probe(worker, backend, size, mtime):
    environment = os.environ.copy()
    environment['PATH'] = str(Path(backend).parent) + os.pathsep + environment.get('PATH', '')
    result = subprocess.run([worker, '--probe'], capture_output=True, text=True, env=environment,
                            timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.returncode == 0 and WET_FABRIC_PROBE in result.stdout.splitlines()


def wet_fabric_capability(executable=None) -> bool:
    """Whether the verified pressure worker also integrates wet fabric."""
    try:
        config = experimental_configuration(executable)
        worker = Path(config['worker']).resolve(strict=True)
        stat = worker.stat()
        return _wet_fabric_probe(str(worker), str(config['backend']), stat.st_size, stat.st_mtime_ns)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return False
