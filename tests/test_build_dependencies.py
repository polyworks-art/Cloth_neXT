from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def test_shared_build_dependencies_cover_ci_and_release():
    requirements=(ROOT/"requirements-build.txt").read_text("utf-8").lower()
    assert "pillow==" in requirements and "resvg-py==" in requirements and "pyinstaller==" in requirements
    for workflow in ("ci.yml","release.yml"):
        assert "pip install -r requirements-build.txt" in (ROOT/".github/workflows"/workflow).read_text("utf-8")

def test_blender_runtime_does_not_import_pillow():
    for path in (ROOT/"cloth_next").rglob("*.py"):
        assert "from PIL" not in path.read_text("utf-8")

def test_release_has_coordinated_windows_build_and_publish_jobs():
    source=(ROOT/".github/workflows/release.yml").read_text("utf-8")
    assert "build-windows-extension:" in source
    assert "needs: [validate, build-windows-extension]" in source
    assert "tools/stage_companion.py" in source
    assert "cloth-next-bake.exe" in source
