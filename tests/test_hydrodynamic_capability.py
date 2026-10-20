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
    with pytest.raises(ValueError, match='Select a GAIA solver'):
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


PROBE_OK=SimpleNamespace(returncode=0,stdout='backend: cuda\nhydrodynamic-fluid-transport: '
    'experimental-velocity-sdf-bricks-v1\nhydrodynamic-sdf-bounds: fail-closed-v1\n')


def installation(tmp_path,monkeypatch,*,listed=True):
    from cloth_next.gaia import owner_test
    monkeypatch.setattr(owner_test,'enabled',lambda:False)
    monkeypatch.delenv('CLOTH_NEXT_EXPERIMENTAL_HYDRO_MANIFEST',raising=False)
    capability_module._verify.cache_clear()
    root=tmp_path/'gaia'
    release=root/'target'/'cuda'/'release';release.mkdir(parents=True)
    import os
    ext='.exe' if os.name=='nt' else ''
    files={'target/cuda/release/ppf-cts-server'+ext:b'server',
           'target/cuda/release/ppf-contact-solver'+ext:b'worker',
           'bin/'+('libsimbackend_cuda.dll' if os.name=='nt' else 'libsimbackend_cuda.so'):b'backend',
           'python/'+('python.exe' if os.name=='nt' else 'bin/python3'):b'python'}
    lines=[]
    for name,data in files.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        lines.append(hashlib.sha256(data).hexdigest()+'  '+name)
    for package in ('numpy','scipy'):(root/'python'/'Lib'/'site-packages'/package).mkdir(parents=True)
    if listed:(root/'SHA256SUMS.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return root, release/('ppf-cts-server'+ext)


def test_selected_installation_with_pressure_worker_qualifies(tmp_path,monkeypatch):
    root,server=installation(tmp_path,monkeypatch)
    monkeypatch.setattr(capability_module.subprocess,'run',lambda *a,**k:PROBE_OK)
    assert hydrodynamic_capability(server).available
    config=capability_module.experimental_configuration(server)
    assert config['installation'] and not config['dependencies']
    assert config['python'].startswith(str(root))


def test_installation_worker_without_pressure_transport_is_rejected(tmp_path,monkeypatch):
    _root,server=installation(tmp_path,monkeypatch)
    monkeypatch.setattr(capability_module.subprocess,'run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout='backend: cuda\n'))
    assert not hydrodynamic_capability(server).available


def test_installation_binaries_must_match_listed_hashes(tmp_path,monkeypatch):
    _root,server=installation(tmp_path,monkeypatch)
    monkeypatch.setattr(capability_module.subprocess,'run',lambda *a,**k:PROBE_OK)
    server.with_name(server.name.replace('ppf-cts-server','ppf-contact-solver')).write_bytes(b'changed')
    capability=hydrodynamic_capability(server)
    assert not capability.available and 'identity changed' in capability.reason


def test_installation_without_hash_list_is_rejected(tmp_path,monkeypatch):
    _root,server=installation(tmp_path,monkeypatch,listed=False)
    monkeypatch.setattr(capability_module.subprocess,'run',lambda *a,**k:PROBE_OK)
    assert not hydrodynamic_capability(server).available
