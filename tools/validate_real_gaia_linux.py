# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Explicit Linux CI gate for the manifest-pinned official Gaia solver."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from cloth_next.platform_support import platform_spec
from cloth_next.ppf.bootstrap import find_release_executable
from cloth_next.ppf.health import start_owned_and_wait
from cloth_next.ppf.layout import BundledSolverLayout
from cloth_next.ppf.process import SolverProcessConfig, SolverProcessManager
from cloth_next.updater.archive import extract_to_staging
from cloth_next.updater.download import download_asset, verify_sha256
from cloth_next.updater.solver_manifest import load_bundled_manifest
from tools.run_ppf_vertical_slice import run as run_vertical_slice


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)

    spec = platform_spec()
    if spec.solver_platform != "linux-x86_64":
        raise SystemExit(f"this gate requires linux-x86_64, got {spec.solver_platform}")
    manifest = load_bundled_manifest(expected_cloth_next_version="2.7.8")
    entry = manifest.entry_for(spec.solver_platform)
    assert entry is not None and entry.release_id == "ppf-0.22-gaia"
    archive = work / entry.official_asset_name
    staging = work / "staging"
    extracted = None
    manager = None
    try:
        download_asset(entry, archive)
        assert archive.stat().st_size == entry.download_size
        verify_sha256(archive, entry.sha256)
        extracted = extract_to_staging(archive, staging)
        executable = find_release_executable(extracted, entry.archive_layout_version)
        assert executable.name == spec.solver_filename
        assert os.access(executable, os.X_OK)
        layout = BundledSolverLayout.from_executable(executable)
        config = SolverProcessConfig(
            executable, layout.root_directory, port=_free_port(),
            environment=layout.process_environment())
        manager = SolverProcessManager(config)
        versions = manager.executable_version()
        assert versions == (entry.solver_package_version,
                            entry.protocol_version, entry.schema_version)
        health = start_owned_and_wait(manager, project_name="cloth-next-gaia-ci")
        assert health.reachable and health.compatible
        manager.stop()
        manager = None
        report = run_vertical_slice(executable, work / "vertical-slice",
                                    frame_count=2, cloth_divisions=2)
        assert report["result"] == "PASS"
        print(json.dumps({
            "result": "PASS",
            "release_id": entry.release_id,
            "official_release_tag": entry.official_release_tag,
            "official_asset_name": entry.official_asset_name,
            "download_size": archive.stat().st_size,
            "sha256": entry.sha256,
            "executable": str(executable.relative_to(extracted)),
            "versions": versions,
            "health": {"reachable": health.reachable,
                       "compatible": health.compatible},
            "vertical_slice": report,
        }, indent=2))
        return 0
    finally:
        if manager is not None:
            manager.stop()
        if extracted is not None and extracted.exists():
            shutil.rmtree(extracted)
        archive.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
