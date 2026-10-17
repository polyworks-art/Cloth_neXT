# SPDX-License-Identifier: GPL-3.0-or-later
"""Local owner evaluation only; no downloader, licensing or release channel."""
import hashlib
import json
from pathlib import Path

_configured_manifest = ''
MARKER = Path(__file__).resolve().parents[1] / 'gaia_owner_test.json'


def enabled():
    return MARKER.is_file()


def configure(path):
    """Called on Blender's main thread; worker threads never access RNA."""
    global _configured_manifest
    _configured_manifest = str(path)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def configuration():
    if not enabled():
        raise ValueError('This package is not an experimental GAIA owner build')
    if not _configured_manifest:
        raise ValueError('Select the separate GAIA owner runtime manifest in Preferences')
    marker = json.loads(MARKER.read_text(encoding='utf-8'))
    if marker.get('kind') != 'ClothNeXt.OwnerBuild.v1':
        raise ValueError('Invalid GAIA owner build marker')
    path = Path(_configured_manifest).expanduser().resolve(strict=True)
    config = json.loads(path.read_text(encoding='utf-8'))
    if config.get('kind') != 'ClothNeXt.OwnerHydrodynamics.v1':
        raise ValueError('Select a GAIA owner runtime manifest, not an official solver package')
    for role in ('runtime', 'server', 'worker', 'backend', 'python', 'dependencies', 'dataset'):
        candidate = Path(config[role]).expanduser()
        config[role] = str((path.parent / candidate).resolve(strict=True))
    dataset = Path(config['dataset'])
    if digest(dataset) != marker.get('dataset_sha256'):
        raise ValueError('Owner NEON dataset differs from the dataset approved for this build')
    config['preparer'] = str(Path(__file__).with_name('prepare_experimental.py'))
    config['owner_test'] = True
    return config


def validate_scene(config, descriptor, scene):
    dataset = json.loads(Path(config['dataset']).read_text(encoding='utf-8'))
    identity = dict(first=descriptor['first'], last=descriptor['last'],
                    data_hash=scene.data_hash, param_hash=scene.param_hash)
    bridge = getattr(scene, 'official_bridge_json', '')
    if bridge:
        binding = json.loads(bridge)
        if binding['prepared_param_hash'] != scene.param_hash:
            raise ValueError('Owner solver parameters changed after the validated scene conversion')
        identity.update(data_hash=binding['source_data_hash'],
                        param_hash=binding['source_param_hash'],
                        binding_sha256=binding_digest(binding))
    if identity not in dataset.get('validated_scenes', []):
        raise ValueError('Unsupported GAIA owner scene or settings. Use the frozen NEON '
                         'scene, original materials/attachments and an explicitly validated frame range.')
    if descriptor['fps'] != 30 or descriptor['model'] != dict(
            density_kg_m3=1000., normal_coefficient=1., tangential_coefficient=0.,
            wet_area_fraction=1., velocity_m_s=[0., 0., 0.], volume_mode=1):
        raise ValueError('Owner hydrodynamics requires the validated 30 Hz one-way Cn=1, Ct=0 model')


def validate_dataset(path, cache, first, count):
    """Verify actual consumed cache/surface/bounds bytes before transport creation."""
    path = Path(path).resolve(strict=True)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('kind') != 'ClothNeXt.OwnerNEONDataset.v1' or data.get('release_approved') is not False:
        raise ValueError('Requires the frozen, non-production owner NEON dataset')
    if count < 2 or first < 130 or first + count - 1 > 160:
        raise ValueError('NEON owner coverage is limited to frames 130–159 plus real 160 lookahead')
    # ZIP timestamps/paths are not physics. Bind the consumed arrays and grids,
    # then GaiaContainer.frame verifies those checksums against actual bytes.
    from .container import GaiaContainer
    with GaiaContainer(cache) as container:
        frames = {record['frame']: record for record in container.manifest['frames']}
        for number in range(first, first + count):
            record = frames.get(number)
            if record is None or frame_identity(record) != data['fluid_frames'].get(str(number)):
                raise ValueError(f'Selected cache differs from validated NEON fluid frame {number}')
    surface = (path.parent / data['surfaces']).resolve(strict=True)
    bounds = (path.parent / data['bounds']).resolve(strict=True)
    for frame in range(first, first + count):
        for directory, name, hashes in (
                (surface, f'{frame:06}.bobj', data['surface_sha256']),
                (bounds, f'bounds{frame:06}.bbox', data['bounds_sha256'])):
            if not hashes.get(name) or digest(directory / name) != hashes[name]:
                raise ValueError(f'Owner FLIP data missing or changed: {name}')
    return surface, bounds


def frame_identity(record):
    return dict(min=record['min'], max=record['max'], time=record['time'],
                arrays={name: entry['sha256'] for name, entry in record['arrays'].items()})


def binding_digest(binding):
    return hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()
