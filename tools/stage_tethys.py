# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage a prebuilt TETHYS Engine binary at its sole approved extension location.

The engine is built from its separate, access-controlled source repository
(``python build.py`` there). Only the compiled binary and this manifest
enter the extension; no engine source is ever staged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.platform_support import PlatformSpec, platform_spec, spec_for_blender_platform
from cloth_next.tethys.engine import FILENAMES, MANIFEST
from cloth_next.tethys.protocol import PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parents[1]


def stage(source: Path, extension: Path = ROOT / "cloth_next", *,
          platform: PlatformSpec | None = None, probe: bool = True) -> Path:
    selected = platform or platform_spec()
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    filename = FILENAMES[selected.blender_platform]
    target = extension / "bin" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if selected.executable_permission_required:
        os.chmod(target, target.stat().st_mode | 0o755)
    if probe and selected == platform_spec():
        # Probe the staged copy: it carries the executable mode bits.
        line = subprocess.run([str(target), "--version"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
        if not line.endswith(f"protocol {PROTOCOL_VERSION}"):
            target.unlink()
            raise ValueError(f"engine protocol mismatch: {line!r}")
    data = target.read_bytes()
    payload = {"schema_version": 1, "filename": filename, "platform": selected.blender_platform,
               "file_size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
               "protocol": PROTOCOL_VERSION}
    (extension / MANIFEST).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--platform", help="windows-x64 or linux-x64 (default: host)")
    args = parser.parse_args()
    chosen = spec_for_blender_platform(args.platform) if args.platform else None
    print(stage(args.source, platform=chosen))
