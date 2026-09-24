import json
import os
from pathlib import Path

import pytest

from cloth_next.bake.companion_bundle import validate_bundle
from tools.stage_companion import stage
from cloth_next.platform_support import LINUX_X64, WINDOWS_X64

def test_stage_and_validate_exact_bundled_companion(tmp_path):
    extension=tmp_path/"cloth_next"; extension.mkdir()
    (extension/"blender_manifest.toml").write_text('version="0.2.0-beta.3"',encoding="utf-8")
    source=tmp_path/"companion.exe"; source.write_bytes(b"MZcloth-next-owned")
    target=stage(source,extension,platform=WINDOWS_X64)
    assert target==extension/"bin/cloth-next-bake.exe"
    assert validate_bundle(extension,"0.2.0-beta.3",platform=WINDOWS_X64)==target

def test_modified_companion_is_rejected(tmp_path):
    extension=tmp_path/"cloth_next"; extension.mkdir()
    (extension/"blender_manifest.toml").write_text('version="0.2.0-beta.3"',encoding="utf-8")
    source=tmp_path/"companion.exe"; source.write_bytes(b"MZoriginal")
    target=stage(source,extension,platform=WINDOWS_X64); target.write_bytes(b"MZmodified")
    with pytest.raises(ValueError,match="mismatch"): validate_bundle(extension,"0.2.0-beta.3",platform=WINDOWS_X64)

def test_arbitrary_filename_in_manifest_is_rejected(tmp_path):
    extension=tmp_path/"cloth_next"; extension.mkdir()
    (extension/"blender_manifest.toml").write_text('version="0.2.0-beta.3"',encoding="utf-8")
    source=tmp_path/"companion.exe"; source.write_bytes(b"MZoriginal")
    stage(source,extension,platform=WINDOWS_X64)
    path=extension/"companion_manifest.json"; payload=json.loads(path.read_text())
    payload["filename"]="other.exe"; path.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match="identity"): validate_bundle(extension,"0.2.0-beta.3",platform=WINDOWS_X64)

@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_stage_rejects_invalid_source(tmp_path, kind):
    extension=tmp_path/"cloth_next"; extension.mkdir()
    (extension/"blender_manifest.toml").write_text('version="0.2.0-beta.3"')
    source=tmp_path/"input.exe"
    if kind == "directory": source.mkdir()
    with pytest.raises(FileNotFoundError):
        stage(source, extension, platform=WINDOWS_X64)


def test_linux_stage_identity_and_executable_mode(tmp_path):
    extension = tmp_path / "cloth_next"
    extension.mkdir()
    (extension / "blender_manifest.toml").write_text('version="2.7.8"')
    source = tmp_path / "companion"
    source.write_bytes(b"\x7fELFfixture")
    target = stage(source, extension, platform=LINUX_X64)
    assert target.name == "cloth-next-bake"
    if os.name == "posix":
        assert target.stat().st_mode & 0o111
    assert validate_bundle(extension, "2.7.8", platform=LINUX_X64) == target


@pytest.mark.skipif(os.name != "posix", reason="POSIX executable modes only")
def test_linux_validation_restores_mode_after_blender_style_zip_install(tmp_path):
    extension = tmp_path / "cloth_next"
    extension.mkdir()
    source = tmp_path / "companion"
    source.write_bytes(b"\x7fELFfixture")
    target = stage(source, extension, platform=LINUX_X64)
    target.chmod(target.stat().st_mode & ~0o111)
    assert not target.stat().st_mode & 0o111
    assert validate_bundle(extension, "2.7.8", platform=LINUX_X64) == target
    assert target.stat().st_mode & 0o111


@pytest.mark.parametrize("field,value", [
    ("platform", "windows-x64"), ("filename", "cloth-next-bake.exe"),
    ("cloth_next_version", "0.0.0"), ("modes", ["bake"]),
])
def test_linux_manifest_wrong_identity_is_rejected(tmp_path, field, value):
    extension = tmp_path / "cloth_next"
    extension.mkdir()
    (extension / "blender_manifest.toml").write_text('version="2.7.8"')
    source = tmp_path / "companion"
    source.write_bytes(b"\x7fELFfixture")
    stage(source, extension, platform=LINUX_X64)
    path = extension / "companion_manifest.json"
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        validate_bundle(extension, "2.7.8", platform=LINUX_X64)
