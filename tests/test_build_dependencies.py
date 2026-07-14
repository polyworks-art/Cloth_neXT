from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def workflow(name):
    return (ROOT/".github/workflows"/name).read_text("utf-8")


def test_shared_build_dependencies_cover_ci_and_candidate():
    requirements=(ROOT/"requirements-build.txt").read_text("utf-8").lower()
    assert "pillow==" in requirements and "resvg-py==" in requirements and "pyinstaller==" in requirements
    for name in ("ci.yml", "build-release-candidate.yml"):
        assert "pip install -r requirements-build.txt" in workflow(name)


def test_numpy_is_declared_wherever_it_is_imported():
    """NumPy is imported at module scope by shipped code and by tests.

    Blender bundles it, so the add-on runs fine — but CI installs only
    requirements-build.txt. Leaving it undeclared there breaks collection of
    every module that touches the PC2 writer, the PPF decoders, or the mesh
    topology hash, which is exactly how CI went red once already.
    """
    importers=[path.relative_to(ROOT).as_posix()
               for directory in ("cloth_next", "tests")
               for path in (ROOT/directory).rglob("*.py")
               if "import numpy" in path.read_text("utf-8")]
    assert importers, "expected NumPy importers to exist"
    requirements=(ROOT/"requirements-build.txt").read_text("utf-8").lower()
    assert "numpy==" in requirements, (
        f"NumPy is imported by {importers} but is not pinned in "
        "requirements-build.txt")


def test_blender_runtime_does_not_import_pillow():
    for path in (ROOT/"cloth_next").rglob("*.py"):
        assert "from PIL" not in path.read_text("utf-8")


def test_source_validation_excludes_built_artifacts():
    pytest_config=(ROOT/"pyproject.toml").read_text("utf-8")
    assert 'addopts = \'-ra -m "not built_artifact"\'' in pytest_config
    for name in ("ci.yml", "build-release-candidate.yml"):
        source=workflow(name)
        assert 'pytest -m "not integration and not built_artifact"' in source
        assert "validate_extension.py cloth_next --phase source" in source


def test_candidate_orders_staging_before_explicit_artifact_tests():
    source=workflow("build-release-candidate.yml")
    assert source.index("tools/stage_companion.py") < source.index("pytest -m built_artifact")
    assert '--extension-zip' in source
    assert "verify-release-candidate:" in source


def test_preflight_cannot_publish_and_release_reuses_candidate():
    preflight=workflow("release-preflight.yml")
    release=workflow("release.yml")
    assert "uses: ./.github/workflows/build-release-candidate.yml" in preflight
    assert "uses: ./.github/workflows/build-release-candidate.yml" in release
    for forbidden in ("gh release create", "gh-pages", "release upload"):
        assert forbidden not in preflight
    assert "needs: candidate" in release
    assert "check_release_preflight.py" in release
