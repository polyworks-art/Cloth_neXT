"""Owner-only gating and physics source identity, independent of Blender."""
import json
from types import SimpleNamespace

import pytest

from cloth_next.gaia import owner_test


def test_regular_package_cannot_enable_owner_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_test, 'MARKER', tmp_path / 'missing.json')
    owner_test.configure(str(tmp_path / 'runtime.json'))
    with pytest.raises(ValueError, match='not an experimental'):
        owner_test.configuration()


def test_owner_manifest_is_bound_to_build_dataset(tmp_path, monkeypatch):
    dataset = tmp_path / 'dataset.json'
    dataset.write_text('{}')
    marker = tmp_path / 'marker.json'
    marker.write_text(json.dumps(dict(kind='ClothNeXt.OwnerBuild.v1',
                                     dataset_sha256=owner_test.digest(dataset))))
    config = dict(kind='ClothNeXt.OwnerHydrodynamics.v1', dataset='dataset.json')
    for name in ('runtime', 'server', 'worker', 'backend', 'python', 'dependencies'):
        config[name] = '.'
    path = tmp_path / 'runtime.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(owner_test, 'MARKER', marker)
    monkeypatch.setattr(owner_test, '_configured_manifest', str(path))
    assert owner_test.configuration()['owner_test']
    dataset.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='differs'):
        owner_test.configuration()


def test_scene_settings_and_range_must_match_validated_export(tmp_path):
    path = tmp_path / 'data.json'
    identity = dict(first=130, last=159, data_hash='geometry', param_hash='materials')
    path.write_text(json.dumps(dict(validated_scenes=[identity])))
    config = dict(dataset=str(path))
    scene = SimpleNamespace(data_hash='geometry', param_hash='materials')
    descriptor = dict(first=130, last=159, fps=30, model=dict(
        density_kg_m3=1000., normal_coefficient=1., tangential_coefficient=0.,
        wet_area_fraction=1., velocity_m_s=[0., 0., 0.], volume_mode=1))
    owner_test.validate_scene(config, descriptor, scene)
    scene.param_hash = 'different material'
    with pytest.raises(ValueError, match='Unsupported'):
        owner_test.validate_scene(config, descriptor, scene)
    scene.param_hash = 'materials'
    descriptor['model']['normal_coefficient'] = 2
    with pytest.raises(ValueError, match='Cn=1'):
        owner_test.validate_scene(config, descriptor, scene)


def test_fluid_identity_ignores_zip_member_name_but_preserves_physics():
    record = dict(min=[0, 0, 0], max=[1, 1, 1], time=0,
                  arrays=dict(velocity=dict(member='first.npy', sha256='velocity')))
    expected = owner_test.frame_identity(record)
    record['arrays']['velocity']['member'] = 'relocated.npy'
    assert owner_test.frame_identity(record) == expected
    record['arrays']['velocity']['sha256'] = 'other'
    assert owner_test.frame_identity(record) != expected


def test_official_conversion_binding_is_verified_including_materials(tmp_path):
    binding = dict(source_data_hash='source', source_param_hash='materials',
                   prepared_param_hash='wire', attachments=[1, 2])
    path = tmp_path / 'dataset.json'
    path.write_text(json.dumps(dict(validated_scenes=[dict(first=130, last=159,
        data_hash='source', param_hash='materials',
        binding_sha256=owner_test.binding_digest(binding))])))
    scene = SimpleNamespace(data_hash='converted', param_hash='wire',
                            official_bridge_json=json.dumps(binding))
    descriptor = dict(first=130, last=159, fps=30, model=dict(
        density_kg_m3=1000., normal_coefficient=1., tangential_coefficient=0.,
        wet_area_fraction=1., velocity_m_s=[0., 0., 0.], volume_mode=1))
    owner_test.validate_scene(dict(dataset=str(path)), descriptor, scene)
    binding['attachments'] = []
    scene.official_bridge_json = json.dumps(binding)
    with pytest.raises(ValueError, match='Unsupported'):
        owner_test.validate_scene(dict(dataset=str(path)), descriptor, scene)
    scene.param_hash = 'changed after conversion'
    with pytest.raises(ValueError, match='parameters changed'):
        owner_test.validate_scene(dict(dataset=str(path)), descriptor, scene)


@pytest.fixture
def dataset(tmp_path):
    import numpy as np
    from cloth_next.gaia.container import write_water_container, GaiaContainer
    from cloth_next.gaia.water_field import WaterVelocityField
    field = WaterVelocityField((0., 0., 0.), (1., 1., 1.),
        np.zeros((2, 2, 2, 3), dtype='<f4'), np.ones((2, 2, 2), dtype='<f4'))
    cache = tmp_path / 'water.gaia'
    write_water_container(cache, {}, [(130, 0., field), (131, 1/30, field)])
    data = dict(kind='ClothNeXt.OwnerNEONDataset.v1', release_approved=False,
        surfaces='.', bounds='.', surface_sha256={}, bounds_sha256={})
    with GaiaContainer(cache) as container:
        data['fluid_frames'] = {str(r['frame']): owner_test.frame_identity(r)
                                for r in container.manifest['frames']}
    for frame in (130, 131):
        for name, key in ((f'{frame:06}.bobj', 'surface_sha256'),
                          (f'bounds{frame:06}.bbox', 'bounds_sha256')):
            path = tmp_path / name
            path.write_bytes(str(frame).encode())
            data[key][name] = owner_test.digest(path)
    manifest = tmp_path / 'dataset.json'
    manifest.write_text(json.dumps(data))
    return manifest, cache


def test_dataset_requires_real_lookahead_and_unchanged_inputs(dataset):
    manifest, cache = dataset
    owner_test.validate_dataset(manifest, cache, 130, 2)
    (manifest.parent / '000131.bobj').write_bytes(b'tampered')
    with pytest.raises(ValueError, match='changed'):
        owner_test.validate_dataset(manifest, cache, 130, 2)


def test_missing_bounds_fail_before_native_solver(dataset):
    manifest, cache = dataset
    (manifest.parent / 'bounds000131.bbox').unlink()
    with pytest.raises(FileNotFoundError):
        owner_test.validate_dataset(manifest, cache, 130, 2)


def test_other_fluid_grid_and_single_sample_fail(dataset):
    manifest, cache = dataset
    data = json.loads(manifest.read_text())
    data['fluid_frames']['130']['min'] = [-1, 0, 0]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='differs'):
        owner_test.validate_dataset(manifest, cache, 130, 2)
    with pytest.raises(ValueError, match='lookahead'):
        owner_test.validate_dataset(manifest, cache, 130, 1)
