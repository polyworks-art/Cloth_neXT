# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the CI-built companion at its sole approved extension location."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tomllib
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.platform_support import PlatformSpec, platform_spec

ROOT=Path(__file__).resolve().parents[1]
def stage(source: Path, extension: Path=ROOT/"cloth_next", *,
          platform: PlatformSpec | None = None) -> Path:
    selected = platform or platform_spec()
    if not source.is_file(): raise FileNotFoundError(source)
    version=tomllib.loads((extension/"blender_manifest.toml").read_text("utf-8"))["version"]
    target=extension/"bin"/selected.companion_filename; target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target); data=target.read_bytes()
    if selected.executable_permission_required:
        os.chmod(target, target.stat().st_mode | 0o755)
    payload={"schema_version":2,"cloth_next_version":version,"filename":selected.companion_filename,
             "platform":selected.blender_platform,"file_size":len(data),
             "sha256":hashlib.sha256(data).hexdigest(),
             "modes":["bake","veyra","welcome","whats-new"]}
    manifest=extension/"companion_manifest.json"
    manifest.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return target

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("source",type=Path); args=parser.parse_args()
    print(stage(args.source))
