# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

import socket
import tempfile
from pathlib import Path

import pytest

from cloth_next.ppf.health import start_owned_and_wait
from cloth_next.ppf.layout import BundledSolverLayout, PLATFORM_DIRECTORY
from cloth_next.ppf.resolver import (SolverResolutionContext, SolverResolver,
                                     development_executable_from_environment)
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager
from cloth_next.core.errors import ClothNextError


def _probe(path):
    layout = BundledSolverLayout.from_root(path.parent)
    manager = SolverProcessManager(SolverProcessConfig(path, layout.root_directory,
        environment=layout.process_environment()))
    return manager.executable_version()


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _resolved_solver():
    repo = Path(__file__).parents[2]
    development = development_executable_from_environment()
    if development is None:
        local_tree = repo / PLATFORM_DIRECTORY / "ppf-cts-server.exe"
        development = local_tree if local_tree.is_file() else None
    resolved = SolverResolver(_probe).resolve(SolverResolutionContext(
        development_executable=development))
    if resolved is None or resolved.executable_path is None:
        pytest.skip(
            "no CLOTH_NEXT_PPF_EXECUTABLE or local development solver "
            "configured")
    return resolved


@pytest.mark.integration
def test_real_pinned_ppf_health():
    resolved = _resolved_solver()
    layout = BundledSolverLayout.from_root(resolved.root_directory)
    runtime = Path(tempfile.mkdtemp(prefix="ClothNeXt-test-"))
    manager = SolverProcessManager(SolverProcessConfig(resolved.executable_path,
        resolved.root_directory, port=_free_port(), progress_file=runtime / "progress.log",
        environment=layout.process_environment()))
    try:
        package_version, protocol_version, schema_version = manager.executable_version()
        health = start_owned_and_wait(manager, project_name="cloth-next-real-health")
        assert health.reachable
        assert health.compatible
        assert health.package_version == package_version
        assert health.protocol_version == protocol_version
        assert health.schema_version == schema_version
    finally:
        manager.stop()
        import shutil
        shutil.rmtree(runtime, ignore_errors=True)


@pytest.mark.integration
def test_real_owned_launch_rejects_occupied_port_without_touching_listener():
    resolved = _resolved_solver()
    layout = BundledSolverLayout.from_root(resolved.root_directory)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        port = listener.getsockname()[1]
        manager = SolverProcessManager(SolverProcessConfig(
            resolved.executable_path, resolved.root_directory, port=port,
            environment=layout.process_environment()))

        with pytest.raises(ClothNextError) as caught:
            start_owned_and_wait(
                manager, project_name="cloth-next-real-port-conflict")

        assert caught.value.record.user_message == (
            f"Port {port} is already in use.")
        assert "PORT_ALREADY_OCCUPIED" in (
            caught.value.record.technical_message)
        assert manager.poll().process_id is None
        # Drain the reachability and health probes from the listener backlog.
        listener.settimeout(1.0)
        for _index in range(2):
            connection, _address = listener.accept()
            connection.close()
        # A new connection proves Cloth NeXt neither adopted nor stopped the
        # unrelated listener.
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass
