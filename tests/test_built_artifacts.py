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


def test_release_candidate_contains_current_feature_set(extension_zip):
    """Never publish a partial Dev build from a stale source checkout."""
    required_files = {
        "materials/product_shell_presets.toml",
        "materials/product_fabric_presets.toml",
        "materials/product_interior_presets.toml",
        "materials/product_protective_presets.toml",
        "materials/product_performance_presets.toml",
        "blender/pin_constraints.py",
        "pinning.py",
        "ppf/schema/data.py",
        "ppf/schema/params.py",
        "blender/solver_test.py",
        "blender/character_collision_cage.py",
        "blender/collider_proxy.py",
        "blender/object_properties.py",
        "blender/physics_ui.py",
        "blender/registration.py",
    }

    with zipfile.ZipFile(extension_zip) as bundle:
        names = set(bundle.namelist())
        assert required_files <= names, sorted(required_files - names)

        product_shell = bundle.read(
            "materials/product_shell_presets.toml").decode("utf-8")
        pin_controls = bundle.read(
            "blender/pin_constraints.py").decode("utf-8")
        pin_model = bundle.read("pinning.py").decode("utf-8")
        scene_wire = bundle.read("ppf/schema/data.py").decode("utf-8")
        pin_wire = bundle.read("ppf/schema/params.py").decode("utf-8")
        solver_export = bundle.read("blender/solver_test.py").decode("utf-8")
        cage = bundle.read(
            "blender/character_collision_cage.py").decode("utf-8")
        proxy = bundle.read("blender/collider_proxy.py").decode("utf-8")
        properties = bundle.read(
            "blender/object_properties.py").decode("utf-8")
        physics_ui = bundle.read("blender/physics_ui.py").decode("utf-8")
        registration = bundle.read(
            "blender/registration.py").decode("utf-8")

    assert "PRODUCT_GORETEX_PRO_3L" in product_shell
    assert "product_sample = true" in product_shell

    assert '("SOFT", "Soft Pin"' in pin_controls
    assert '("HARD", "Hard Pin"' in pin_controls
    assert '"pin_pull_strength"' in pin_controls
    assert "class PinConstraintType" in pin_model
    assert 'config["pull_strength"]' in pin_wire
    assert "pin_constraints.CLASSES" in registration

    assert "conservative 26-DOP" in cage
    assert "transform-only STATIC colliders" in cage
    assert "CHARACTER_CAGE" in proxy
    assert "CHARACTER_CAGE" in properties
    assert "Simulation Proxy" in physics_ui
    assert 'layout.prop(settings, "collider_proxy_type")' in physics_ui
    assert "Generate Character Cage" in physics_ui

    assert "def _schema2_full_frame_indices" in scene_wire
    for timeline_key in (
            "_sample_frame_offset", "_logical_frame_count",
            "_samples_per_frame", "_capture_fps"):
        assert f'"{timeline_key}"' in scene_wire
    assert "Schema 2 animated Collider is missing canonical" in scene_wire
    assert "expected_samples = (" in scene_wire
    assert "settings.fps * settings.time_scale" in pin_wire
    assert '"_sample_frame_offset": frame_offsets' in solver_export
    assert "SCENE_EXPORT_CACHE_SCHEMA = 4" in solver_export
    assert '"animation_digest"' in solver_export
    assert "content_digest=motion_hasher.hexdigest()" in solver_export


def test_release_candidate_passes_complete_artifact_scan(extension_zip):
    assert scan_zip(extension_zip) == []
