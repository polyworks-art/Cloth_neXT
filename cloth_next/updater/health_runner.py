"""Run the real solver health lifecycle for a candidate executable.

Starts the candidate server on a free ephemeral port, waits for readiness plus
a compatible status query, then stops the owned process again. Used as the
mandatory gate before a managed installation is activated. No ``bpy`` here.
"""

from __future__ import annotations

import socket
from pathlib import Path

from cloth_next.core.errors import ClothNextError
from cloth_next.ppf.health import start_owned_and_wait
from cloth_next.ppf.layout import BundledSolverLayout
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def bundle_root_for(executable: Path) -> Path:
    parent = executable.parent
    if parent.name == "release" and parent.parent.name == "target":
        return parent.parent.parent
    return parent


def run_real_health_check(executable: Path, *, startup_timeout: float = 60.0) -> bool:
    root = bundle_root_for(executable)
    layout = BundledSolverLayout.from_root(root)
    config = SolverProcessConfig(
        executable_path=executable,
        working_directory=root,
        host="127.0.0.1",
        port=free_port(),
        startup_timeout=startup_timeout,
        ownership_mode=ConnectionOwnership.OWNED_PROCESS,
        environment=layout.process_environment(),
    )
    manager = SolverProcessManager(config)
    try:
        health = start_owned_and_wait(manager)
        return bool(health.reachable and health.compatible)
    except (ClothNextError, ValueError, OSError):
        return False
    finally:
        try:
            manager.stop()
        except (ClothNextError, ValueError, OSError):
            pass
