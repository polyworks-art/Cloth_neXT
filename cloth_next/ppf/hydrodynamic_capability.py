# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed gate for a future, independently verified native extension.

Protocol 0.26 and native FluidVolume support do not certify triangle pressure.
The isolated uniform-flow upstream test is not a supported scene transport.
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
        if not owner_test.enabled():
            raise ValueError('Requires verified native triangle forces and surface-based wetting transport')
        config = owner_test.configuration()
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
        return HydrodynamicCapability(True, 'Isolated CUDA pressure / native SDF bounds verified')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return HydrodynamicCapability(False, str(exc))


def require_hydrodynamic_capability(executable=None):
    capability = hydrodynamic_capability(executable)
    if not capability.available:
        raise ValueError('Hydrodynamic Pressure unavailable: '+capability.reason)
