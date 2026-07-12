import tomllib
from pathlib import Path
from zipfile import ZipFile

from tools.validate_extension import validate_source_tree, validate_zip


ROOT = Path(__file__).parents[1]
EXTENSION_ROOT = ROOT / "cloth_next"


def test_extension_source_root():
    validate_source_tree(EXTENSION_ROOT)
    manifest = tomllib.loads((EXTENSION_ROOT / "blender_manifest.toml").read_text(encoding="utf-8"))
    assert manifest["id"] == "cloth_next"
    assert manifest["blender_version_min"] == "5.0.0"


def test_zip_has_manifest_and_entrypoint_at_archive_root(tmp_path):
    archive = tmp_path / "cloth_next.zip"
    with ZipFile(archive, "w") as bundle:
        for path in EXTENSION_ROOT.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(EXTENSION_ROOT))
    validate_zip(archive)

