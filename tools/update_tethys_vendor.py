# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refresh the vendored, prebuilt TETHYS Engine binaries from an engine checkout.

    python tools/update_tethys_vendor.py ../TETHYS-Engine

The engine source lives in the private repository polyworks-art/THETYS. Only
its compiled release binaries (never test builds, never source) are copied to
``vendor/tethys/`` together with ``ENGINE.json`` (engine commit, sizes,
SHA-256, protocol). Release workflows stage them with ``stage_tethys.py``.
Build the engine first there: ``python build.py --tests`` and
``python build.py --linux-alma --tests``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.tethys.engine import FILENAMES
from cloth_next.tethys.protocol import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "tethys"


def update(engine_repo: Path) -> dict:
    engine_repo = engine_repo.resolve()
    commit = subprocess.run(["git", "-C", str(engine_repo), "rev-parse", "HEAD"], capture_output=True,
                            text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(engine_repo), "status", "--porcelain", "--", "src"],
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise SystemExit("Engine sources have uncommitted changes; commit them first")
    manifest = {"schema_version": 1, "engine_repository": "polyworks-art/THETYS",
                "engine_commit": commit, "protocol": PROTOCOL_VERSION, "binaries": {}}
    for platform, filename in FILENAMES.items():
        source = engine_repo / "dist" / platform / filename
        if not source.is_file():
            raise SystemExit(f"missing engine build: {source}")
        target = VENDOR / platform / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        data = target.read_bytes()
        manifest["binaries"][platform] = {"filename": filename, "file_size": len(data),
                                          "sha256": hashlib.sha256(data).hexdigest()}
    (VENDOR / "ENGINE.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("engine_repo", type=Path)
    print(json.dumps(update(parser.parse_args().engine_repo), indent=2))
