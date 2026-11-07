# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage a prebuilt AETHER Engine binary at its sole approved extension location.

The engine is built from its separate source repository (``python build.py``
in the AETHER repository). Only the compiled ``AETHER Engine.exe`` and this
manifest enter the extension; no engine source is staged. The manifest pins
size, SHA-256, protocol, API version and the engine's own build id, and
``cloth_next.aether_engine.engine.locate`` refuses any other file.

Not used by any release workflow yet (Milestone 5 is an experimental
integration); see docs/AETHER_ENGINE_INTEGRATION.md, "Distribution".
"""
# Local development only: release ZIPs no longer bundle native engines; the add-on
# installs them from the server (tools/release_engine.py uploads a build).
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
from cloth_next.aether_engine.engine import API_VERSION, FILENAMES, MANIFEST, PROTOCOL_VERSION, _VERSION_LINE
from cloth_next.platform_support import platform_spec

ROOT = Path(__file__).resolve().parents[1]


def stage(source: Path, extension: Path = ROOT / "cloth_next") -> Path:
    selected = platform_spec().blender_platform
    filename = FILENAMES[selected]
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    target = extension / "bin" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    line = subprocess.run([str(target), "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    match = _VERSION_LINE.match(line)
    if match is None:
        target.unlink()
        raise ValueError(f"not an AETHER Engine binary: {line!r}")
    data = target.read_bytes()
    payload = {"schema_version": 1, "filename": filename, "platform": selected, "file_size": len(data),
               "sha256": hashlib.sha256(data).hexdigest(), "protocol": PROTOCOL_VERSION,
               "api": list(API_VERSION), "engine_version": match.group(1), "build_id": match.group(2)}
    (extension / MANIFEST).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    print(stage(parser.parse_args().source))
