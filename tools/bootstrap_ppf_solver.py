# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Explicitly import an official/local PPF Windows tree into the repository."""

from __future__ import annotations

import argparse
import shutil
import socket
import sys
import tempfile
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloth_next.ppf.bootstrap import (
    UPSTREAM_BASELINE, atomic_replace_directory, copy_directory,
    find_license_files, find_single_executable, safe_extract_zip,
    write_source_metadata,
)
from cloth_next.ppf.compatibility import EXPECTED_PACKAGE, EXPECTED_PROTOCOL, EXPECTED_SCHEMA
from cloth_next.ppf.health import start_owned_and_wait
from cloth_next.ppf.layout import BundledSolverLayout, PLATFORM_DIRECTORY
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _source_into(source: Path, kind: str, destination: Path) -> None:
    if kind == "archive":
        safe_extract_zip(source, destination)
    else:
        root = source.parent if kind == "executable" else source
        copy_directory(root, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--archive", type=Path)
    group.add_argument("--directory", type=Path)
    group.add_argument("--executable", type=Path)
    parser.add_argument("--target", type=Path,
        default=Path(__file__).resolve().parents[1] / PLATFORM_DIRECTORY)
    parser.add_argument("--source-url")
    parser.add_argument("--upstream-commit", default=UPSTREAM_BASELINE)
    parser.add_argument("--skip-health-check", action="store_true",
        help="Create metadata with health_check=not_run; unsuitable for release builds.")
    args = parser.parse_args()
    kind = next(name for name in ("archive", "directory", "executable")
                if getattr(args, name) is not None)
    source = getattr(args, kind).expanduser().resolve()
    target = args.target.expanduser().resolve()
    if not source.exists():
        parser.error(f"source does not exist: {source}")

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".staging-{uuid.uuid4().hex}"
    runtime = Path(tempfile.mkdtemp(prefix="ClothNeXt-bootstrap-"))
    try:
        _source_into(source, kind, staging)
        executable = find_single_executable(staging)
        root_executable = staging / "ppf-cts-server.exe"
        if executable != root_executable:
            shutil.copy2(executable, root_executable)
            executable.unlink()
        licenses = find_license_files(staging)
        if not licenses:
            raise ValueError("no LICENSE or NOTICE files found in source; distribution import refused")
        license_root = staging / "LICENSES"
        license_root.mkdir(exist_ok=True)
        for index, license_file in enumerate(licenses):
            if license_root in license_file.parents:
                continue
            name = license_file.name if not (license_root / license_file.name).exists() else f"{index}-{license_file.name}"
            shutil.copy2(license_file, license_root / name)

        layout = BundledSolverLayout.from_root(staging)
        config = SolverProcessConfig(layout.executable_path, layout.root_directory,
            port=free_port(), progress_file=runtime / "progress.log",
            environment=layout.process_environment())
        manager = SolverProcessManager(config)
        versions = manager.executable_version()
        if versions != (EXPECTED_PACKAGE, EXPECTED_PROTOCOL, EXPECTED_SCHEMA):
            raise ValueError(f"incompatible solver versions: {versions}")
        health_passed = False
        if not args.skip_health_check:
            health = start_owned_and_wait(manager, "cloth-next-bootstrap")
            health_passed = health.compatible
            manager.stop()
            if not health_passed:
                raise ValueError(f"solver health check failed: {health.last_error}")
        source_type = "official_archive" if args.source_url else f"local_{kind}"
        write_source_metadata(layout, source_type=source_type, source_url=args.source_url,
            source_label="redacted", versions=versions, health_passed=health_passed,
            upstream_commit=args.upstream_commit)
        atomic_replace_directory(staging, target)
        print(f"Solver installed: {target}")
        print(f"Version: package={versions[0]} protocol={versions[1]} schema={versions[2]}")
        print(f"Health check: {'passed' if health_passed else 'not run'}")
        return 0
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

