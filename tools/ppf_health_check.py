# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender-free command-line health check for a local PPF 0.11 server."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloth_next.core.errors import ClothNextError
from cloth_next.core.logging import initialize_logging
from cloth_next.ppf.health import start_owned_and_wait
from cloth_next.ppf.layout import PLATFORM_DIRECTORY, BundledSolverLayout
from cloth_next.ppf.models import ConnectionOwnership
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager
from cloth_next.ppf.resolver import (SolverResolutionContext, SolverResolver,
                                     development_executable_from_environment)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--executable", type=Path,
                        help="Explicit solver executable (external installation)")
    source.add_argument("--development", action="store_true",
                        help="Use CLOTH_NEXT_PPF_EXECUTABLE or the local "
                             "solver/windows-x86_64 development tree")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    parser.add_argument("--working-directory", type=Path)
    parser.add_argument("--startup-timeout", type=float, default=20.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    initialize_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    try:
        def probe(path: Path) -> tuple[str, str, str]:
            layout = BundledSolverLayout.from_root(path.parent)
            return SolverProcessManager(SolverProcessConfig(path, layout.root_directory,
                environment=layout.process_environment())).executable_version()

        development = None
        if args.development:
            development = development_executable_from_environment()
            if development is None:
                local = (Path(__file__).resolve().parents[1] / PLATFORM_DIRECTORY
                         / "ppf-cts-server.exe")
                development = local if local.is_file() else None
        resolved = SolverResolver(probe).resolve(SolverResolutionContext(
            external_path=args.executable,
            development_executable=development,
        ))
        if resolved is None or resolved.executable_path is None or resolved.root_directory is None:
            raise ValueError("no solver installation resolved")
        executable = resolved.executable_path
        layout = BundledSolverLayout.from_root(resolved.root_directory)
        config = SolverProcessConfig(
            executable_path=executable,
            working_directory=(args.working_directory or resolved.root_directory),
            host=args.host, port=args.port, startup_timeout=args.startup_timeout,
            ownership_mode=ConnectionOwnership.OWNED_PROCESS,
            environment=layout.process_environment(),
        )
        manager = SolverProcessManager(config)
        health = start_owned_and_wait(manager)
        print(f"Executable: valid ({config.executable_path})")
        print(f"Ownership: {health.ownership.name.lower()}")
        print(f"Process: {'running' if health.process_running else 'external/unknown'}")
        print(f"Server: {'reachable' if health.reachable else 'unreachable'}")
        print(f"Protocol: {health.protocol_version or 'unknown'} "
              f"{'compatible' if health.compatible else 'not fully verified'}")
        print(f"Schema: {health.schema_version or 'unknown'}")
        print(f"Status: {health.wire_status or 'unknown'}")
        print(f"Health: {'ready' if health.compatible else 'limited'}")
        if health.last_error:
            print(f"Notice: {health.last_error.user_message}")
        if health.ownership is ConnectionOwnership.OWNED_PROCESS:
            manager.stop()
        return 0 if health.compatible else 2
    except (ValueError, OSError, ClothNextError) as exc:
        if isinstance(exc, ClothNextError):
            print(f"Error: {exc.record.user_message}", file=sys.stderr)
            if args.verbose:
                print(exc.record.technical_message, file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
