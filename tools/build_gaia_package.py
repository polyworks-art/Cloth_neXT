# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the installation-ready GAIA solver ZIP.

GAIA = the official, verified ST-Tech PPF Contact Solver runtime of one
release (Apache-2.0) with PolyWorks' rebuilt CUDA worker and CUDA backend.
Only the runtime parts the CUDA solver needs are copied; caches, sources,
examples, ROCm, ffmpeg and development tools are excluded. No numerical code
is built or changed here: the two patched binaries are taken as validated.

    python tools/build_gaia_package.py --base <official install root> \
        --worker <ppf-contact-solver.exe> --backend-dll <libsimbackend_cuda.dll> \
        --version 0.26.0-dev.20261018 --revision bf3dad5 --repo <git checkout> \
        --patch <native patch> [--patch ...] --output <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import zipfile

KIND = "ClothNeXt.GaiaBuild.v1"
PLATFORM = "windows-x64"
BACKEND = "cuda12.8"
BASE_RELEASE_TAG = "2026-10-13-12-32"
BASE_ASSET_SHA256 = "d3957d273694b0377e5cbea3e20919d86cac0cfce09d4e4e52ca7868868f0c2c"
PROTOCOL = "0.26"
WORKER = "target/cuda/release/ppf-contact-solver.exe"
BACKEND_DLL = "bin/libsimbackend_cuda.dll"
# Unchanged upstream runtime parts needed by the CUDA server, worker and frontend.
BASE_FILES = ("bin/cudart64_12.dll", "target/cuda/release/ppf-cts-server.exe",
              "target/cuda/release/_ppf_cts_py.dll", "target/cuda/release/.ppf-backend",
              "licenses/ZOZO-Contact-Solver-LICENSE.txt", "THIRD_PARTY_LICENSES.txt")
BASE_TREES = ("python", "frontend")
EXCLUDED_PARTS = {"__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _tree(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"symlink in base runtime: {path}")
        if any(part in EXCLUDED_PARTS for part in relative.parts) or path.suffix in EXCLUDED_SUFFIXES:
            continue
        if path.is_file():
            yield path, relative


def archive_name(build_id: str) -> str:
    return build_id.replace("+", "_")


def full_revision(repo: Path, revision: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{revision}^{{commit}}"],
                               capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def notice(version: str, revision: str, patches: list[str]) -> str:
    return f"""GAIA {version}
==========

GAIA is PolyWorks' separately maintained build of ZOZO's Contact Solver
(PPF Contact Solver, Copyright 2025 Ryoichi Ando, ZOZO, Inc.), licensed under
the Apache License, Version 2.0 (licenses/ZOZO-Contact-Solver-LICENSE.txt).
It is not an official ZOZO or ST-Tech release.

Base: official release {BASE_RELEASE_TAG} (Windows asset SHA-256
{BASE_ASSET_SHA256}), unchanged except for the two files below.

Modified files (rebuilt by PolyWorks from the base source with the patches in
source-changes/, Cloth NeXt revision {revision}):
  {WORKER}
  {BACKEND_DLL}

Changes: deterministic reproducibility research paths (PPF_REPRO_* switches,
opt-in, off unless Cloth NeXt's "Reproducible Bake" is enabled) and the
hydrodynamic force field. Without PPF_REPRO_* switches the solver follows the
upstream unordered execution.

Third-party runtime components are redistributed unchanged under their own
licenses: THIRD_PARTY_LICENSES.txt (NVIDIA CUDA runtime under the CUDA EULA,
Eigen MPL-2.0), and the embedded Python distribution with its packages under
python/ (each package's license in its *.dist-info directory).

Patches: {", ".join(patches)}
"""


def build(args) -> Path:
    base = args.base.resolve()
    revision = full_revision(args.repo, args.revision)
    rev12 = revision[:12]
    build_id = f"gaia-{args.version}+{BACKEND}.{PLATFORM}.{rev12}"
    staging_parent = Path(tempfile.mkdtemp(prefix="gaia-package-", dir=args.output))
    root = staging_parent / build_id
    try:
        for name in BASE_FILES:
            source = base / name
            if not source.is_file():
                raise ValueError(f"base runtime file is missing: {source}")
            _copy(source, root / name)
        for tree in BASE_TREES:
            for path, relative in _tree(base / tree):
                _copy(path, root / tree / relative)
        _copy(args.worker.resolve(), root / WORKER)
        _copy(args.backend_dll.resolve(), root / BACKEND_DLL)
        patches = []
        for patch in args.patch:
            _copy(patch.resolve(), root / "source-changes" / patch.name)
            patches.append(f"source-changes/{patch.name}")
        (root / "GAIA-NOTICE.txt").write_text(notice(args.version, revision, patches),
                                              encoding="utf-8", newline="\n")
        build_file = {
            "kind": KIND, "version": args.version, "build_id": build_id,
            "source_revision": revision, "platform": PLATFORM, "backend": BACKEND,
            "base_release_tag": BASE_RELEASE_TAG, "base_asset_sha256": BASE_ASSET_SHA256,
            "protocol_version": PROTOCOL, "cuda": {"toolkit": "12.8", "runtime": "cudart64_12.dll"},
            "requirements": {"os": "Windows 10/11 x64", "gpu": "NVIDIA GPU with a CUDA 12.8 capable driver",
                             "vc_redist": "Microsoft Visual C++ 2015-2022 Redistributable (x64)"},
            "reproducible_switches": ["PPF_REPRO_FAST_CONTACT", "PPF_REPRO_FAST_SERIAL",
                                      "PPF_REPRO_FILTERED_CONTACT", "PPF_REPRO_ORDER_FILTER",
                                      "PPF_REPRO_ORDER_MODE", "PPF_REPRO_SERIAL_CSR",
                                      "PPF_REPRO_SERIAL_DIRICHLET", "PPF_REPRO_SERIAL_STITCH",
                                      "PPF_REPRO_WALK_SPMV"],
            "files": {WORKER: sha256(root / WORKER), BACKEND_DLL: sha256(root / BACKEND_DLL)},
        }
        (root / "gaia-build.json").write_text(json.dumps(build_file, indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8", newline="\n")
        sums = [f"{sha256(path)}  {path.relative_to(root).as_posix()}"
                for path in sorted(root.rglob("*")) if path.is_file()]
        (root / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
        # Upload portals accept only letters, digits, dot, dash and underscore in
        # file names; the build id keeps its "+" inside gaia-build.json.
        archive = args.output / f"{archive_name(build_id)}.zip"
        partial = archive.with_suffix(".zip.partial")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    # A short top folder keeps the deepest embedded-Python path
                    # well below Windows' 260-character limit during staging.
                    name = PurePosixPath("gaia", *path.relative_to(root).parts).as_posix()
                    bundle.write(path, name)
        os.replace(partial, archive)
        digest = sha256(archive)
        # LF only, so `sha256sum -c` accepts it as well as Get-FileHash users.
        Path(f"{archive}.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n")
        print(json.dumps({"archive": str(archive), "sha256": digest, "size_bytes": archive.stat().st_size,
                          "build_id": build_id, "files": len(sums)}, indent=2))
        return archive
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("base", "worker", "backend-dll", "repo", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--patch", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    build(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
