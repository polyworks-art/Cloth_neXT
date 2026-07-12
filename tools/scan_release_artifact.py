# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reject Cloth NeXt release artifacts that contain any PPF solver material.

Cloth NeXt releases never contain, mirror, repackage, or redistribute the PPF
Contact Solver (docs/RELEASE_POLICY.md section 6). This scanner aborts the
release when the extension ZIP contains solver executables, solver archives,
solver runtime directories, downloaded solver files, logs, caches, progress
files, or temporary installation data.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable
from zipfile import ZipFile

FORBIDDEN_FILE_PATTERNS = (
    "ppf-cts-server.exe",
    "ppf-contact-solver.exe",
    "ppf-contact-solver-*.zip",
    "ppf-contact-solver-*-win64.zip",
    "headless.bat",
    "start.bat",
    "start-jupyterlab.pyw",
    "source.json",
    "current.json",
    "*.dll",
    "*.log",
    "*.pyc",
    "*.partial",
)

FORBIDDEN_DIRECTORIES = (
    "solver",
    "solver/windows-x86_64",
    "downloads",
    "managed_solver",
    "staging",
    "logs",
    "target/release",
    "__pycache__",
)


def _matches_forbidden_directory(lowered_parts: list[str], directory: str) -> bool:
    needle = directory.split("/")
    return any(lowered_parts[start:start + len(needle)] == needle
               for start in range(len(lowered_parts)))


def scan_names(names: Iterable[str]) -> list[str]:
    violations: list[str] = []
    for raw in names:
        name = raw.replace("\\", "/").rstrip("/")
        if not name:
            continue
        path = PurePosixPath(name)
        lowered_parts = [part.lower() for part in path.parts]
        file_pattern = next((pattern for pattern in FORBIDDEN_FILE_PATTERNS
                             if fnmatch.fnmatch(path.name.lower(), pattern)), None)
        if file_pattern is not None:
            violations.append(f"{raw}: forbidden solver/runtime file ({file_pattern})")
            continue
        directory = next((entry for entry in FORBIDDEN_DIRECTORIES
                          if _matches_forbidden_directory(lowered_parts, entry)), None)
        if directory is not None:
            violations.append(f"{raw}: forbidden solver/runtime directory ({directory})")
    return violations


def scan_zip(archive: Path) -> list[str]:
    with ZipFile(archive) as bundle:
        return scan_names(bundle.namelist())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        violations = scan_zip(args.archive)
    except OSError as exc:
        print(f"artifact scan failed: {exc}", file=sys.stderr)
        return 1
    if violations:
        print("FORBIDDEN solver material found in the release artifact:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    print(f"artifact scan passed: {args.archive} contains no PPF solver material")
    return 0


if __name__ == "__main__":
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
