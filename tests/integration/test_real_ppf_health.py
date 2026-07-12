import os
import socket
import tempfile
from pathlib import Path

import pytest

from cloth_next.ppf.health import start_owned_and_wait
from cloth_next.ppf.layout import BundledSolverLayout, PLATFORM_DIRECTORY
from cloth_next.ppf.resolver import SolverResolutionContext, SolverResolver
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager


def _probe(path):
    layout = BundledSolverLayout.from_root(path.parent)
    manager = SolverProcessManager(SolverProcessConfig(path, layout.root_directory,
        environment=layout.process_environment()))
    return manager.executable_version()


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.integration
def test_real_pinned_ppf_health():
    repo = Path(__file__).parents[2]
    extension = repo / "cloth_next"
    configured = Path(os.environ["CLOTH_NEXT_PPF_EXECUTABLE"]) if os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE") else None
    resolved = SolverResolver(_probe).resolve(SolverResolutionContext(
        extension_root=extension, repository_root=repo, external_path=configured))
    if resolved is None or resolved.executable_path is None:
        pytest.skip("no explicit, extension-bundled, or repository-bundled solver")
    layout = BundledSolverLayout.from_root(resolved.root_directory)
    runtime = Path(tempfile.mkdtemp(prefix="ClothNeXt-test-"))
    manager = SolverProcessManager(SolverProcessConfig(resolved.executable_path,
        resolved.root_directory, port=_free_port(), progress_file=runtime / "progress.log",
        environment=layout.process_environment()))
    try:
        health = start_owned_and_wait(manager, project_name="cloth-next-real-health")
        assert health.reachable
        assert health.compatible
        assert health.protocol_version == "0.11"
        assert health.schema_version == "1"
    finally:
        manager.stop()
        import shutil
        shutil.rmtree(runtime, ignore_errors=True)
