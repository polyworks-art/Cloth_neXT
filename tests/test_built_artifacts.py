import hashlib
import json
import tomllib
import zipfile

import pytest

from tools.scan_release_artifact import scan_zip


pytestmark = pytest.mark.built_artifact


def test_release_candidate_contains_verified_companion(extension_zip):
    with zipfile.ZipFile(extension_zip) as bundle:
        names = bundle.namelist()
        assert names.count("bin/cloth-next-bake.exe") == 1
        assert names.count("companion_manifest.json") == 1
        binary = bundle.read("bin/cloth-next-bake.exe")
        metadata = json.loads(bundle.read("companion_manifest.json"))
        version = tomllib.loads(bundle.read("blender_manifest.toml").decode())["version"]
    assert binary.startswith(b"MZ")
    assert metadata["cloth_next_version"] == version
    assert metadata["filename"] == "cloth-next-bake.exe"
    assert metadata["platform"] == "windows-x64"
    assert metadata["file_size"] == len(binary)
    assert metadata["sha256"] == hashlib.sha256(binary).hexdigest()


def test_release_candidate_passes_complete_artifact_scan(extension_zip):
    assert scan_zip(extension_zip) == []
