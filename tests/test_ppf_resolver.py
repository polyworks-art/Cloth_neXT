import json
from pathlib import Path

from cloth_next.ppf.layout import BundledSolverLayout, PLATFORM_DIRECTORY
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.resolver import (
    SolverMode, SolverResolutionContext, SolverResolver, repository_root,
)


def install_fake(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "ppf-cts-server.exe").write_bytes(b"exe")


def resolver():
    return SolverResolver(lambda _path: ("0.1.0", "0.11", "1"))


def test_repository_root_requires_project_markers(tmp_path):
    assert repository_root(tmp_path) is None
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / "cloth_next").mkdir()
    (tmp_path / "cloth_next/blender_manifest.toml").write_text("", encoding="utf-8")
    assert repository_root(tmp_path) == tmp_path.resolve()


def test_priority_external_installation_then_extension_then_repository(tmp_path):
    extension = tmp_path / "extension"
    repository = tmp_path / "repo"
    external = tmp_path / "external"
    for root in (extension / PLATFORM_DIRECTORY, repository / PLATFORM_DIRECTORY, external):
        install_fake(root)
    context = SolverResolutionContext(extension, repository, external, True)
    assert resolver().resolve(context).mode is SolverMode.EXTERNAL_INSTALLATION
    assert resolver().resolve(SolverResolutionContext(extension, repository, None, True)).mode is SolverMode.EXTENSION_BUNDLED
    assert resolver().resolve(SolverResolutionContext(tmp_path / "empty", repository, None, True)).mode is SolverMode.REPOSITORY_BUNDLED


def test_external_server_and_missing_solver(tmp_path):
    external = resolver().resolve(SolverResolutionContext(tmp_path, None, None, True))
    assert external.mode is SolverMode.EXTERNAL_SERVER
    assert external.ownership is ConnectionOwnership.EXTERNAL_SERVER
    assert external.executable_path is None
    assert resolver().resolve(SolverResolutionContext(tmp_path)) is None


def test_extension_solver_is_never_writable(tmp_path):
    extension = tmp_path / "readonly"
    install_fake(extension / PLATFORM_DIRECTORY)
    result = resolver().resolve(SolverResolutionContext(extension))
    assert result.mode is SolverMode.EXTENSION_BUNDLED
    assert not result.writable


def test_layout_runtime_environment_does_not_write_bundle(tmp_path):
    install_fake(tmp_path)
    (tmp_path / "python").mkdir()
    layout = BundledSolverLayout.from_root(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    environment = dict(layout.process_environment())
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert before == after
    assert environment["PYTHONPATH"] == str(tmp_path.resolve())
