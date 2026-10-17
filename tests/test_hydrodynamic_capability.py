import pytest
import hashlib
import json
from types import SimpleNamespace
from cloth_next.ppf import hydrodynamic_capability as capability_module
from cloth_next.ppf.hydrodynamic_capability import (
    hydrodynamic_capability, require_hydrodynamic_capability)


def test_official_fluid_support_does_not_enable_triangle_pressure(monkeypatch):
    from cloth_next.gaia import owner_test
    monkeypatch.setattr(owner_test, 'enabled', lambda: False)
    assert not hydrodynamic_capability().available
    with pytest.raises(ValueError, match='surface-based wetting'):
        require_hydrodynamic_capability()


def isolated_manifest(tmp_path,monkeypatch):
    profile=tmp_path/'profile';profile.mkdir()
    runtime=tmp_path/'runtime';runtime.mkdir()
    config={'kind':'ClothNeXt.IsolatedHydrodynamics.v1','runtime':str(runtime)}
    for role in ('server','worker','backend'):
        path=runtime/role;path.write_bytes(role.encode())
        config[role]=str(path);config[role+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest=profile/'hydro.json';manifest.write_text(json.dumps(config))
    monkeypatch.setenv('BLENDER_USER_RESOURCES',str(profile))
    monkeypatch.setenv('CLOTH_NEXT_EXPERIMENTAL_HYDRO_MANIFEST',str(manifest))
    capability_module._verify.cache_clear()
    return config


def test_matching_transport_without_bounds_guard_is_rejected(tmp_path,monkeypatch):
    isolated_manifest(tmp_path,monkeypatch)
    monkeypatch.setattr(capability_module.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout='backend: cuda\nhydrodynamic-fluid-transport: experimental-velocity-sdf-bricks-v1\n'))
    assert not hydrodynamic_capability().available


def test_identity_and_selected_server_are_enforced(tmp_path,monkeypatch):
    config=isolated_manifest(tmp_path,monkeypatch)
    monkeypatch.setattr(capability_module.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout='backend: cuda\nhydrodynamic-fluid-transport: experimental-velocity-sdf-bricks-v1\nhydrodynamic-sdf-bounds: fail-closed-v1\n'))
    assert hydrodynamic_capability(config['server']).available
    assert not hydrodynamic_capability(tmp_path/'official-server').available
    from pathlib import Path
    Path(config['worker']).write_bytes(b'changed')
    assert not hydrodynamic_capability(config['server']).available
