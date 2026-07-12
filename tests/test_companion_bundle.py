import json
from pathlib import Path

import pytest

from cloth_next.bake.companion_bundle import validate_bundle
from tools.stage_companion import stage

def test_stage_and_validate_exact_bundled_companion(tmp_path):
    extension=tmp_path/"cloth_next"; extension.mkdir()
    (extension/"blender_manifest.toml").write_text('version="0.2.0-beta.3"',encoding="utf-8")
    source=tmp_path/"companion.exe"; source.write_bytes(b"MZcloth-next-owned")
    target=stage(source,extension)
    assert target==extension/"bin/cloth-next-bake.exe"
    assert validate_bundle(extension,"0.2.0-beta.3")==target

def test_modified_companion_is_rejected(tmp_path):
    extension=tmp_path/"cloth_next"; extension.mkdir()
    (extension/"blender_manifest.toml").write_text('version="0.2.0-beta.3"',encoding="utf-8")
    source=tmp_path/"companion.exe"; source.write_bytes(b"MZoriginal")
    target=stage(source,extension); target.write_bytes(b"MZmodified")
    with pytest.raises(ValueError,match="mismatch"): validate_bundle(extension,"0.2.0-beta.3")

def test_arbitrary_filename_in_manifest_is_rejected(tmp_path):
    extension=tmp_path/"cloth_next"; extension.mkdir()
    (extension/"blender_manifest.toml").write_text('version="0.2.0-beta.3"',encoding="utf-8")
    source=tmp_path/"companion.exe"; source.write_bytes(b"MZoriginal")
    stage(source,extension)
    path=extension/"companion_manifest.json"; payload=json.loads(path.read_text())
    payload["filename"]="other.exe"; path.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match="identity"): validate_bundle(extension,"0.2.0-beta.3")
