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

ROOT=Path(__file__).resolve().parents[1]
FILENAME="cloth-next-bake.exe"

def stage(source: Path, extension: Path=ROOT/"cloth_next") -> Path:
    if not source.is_file(): raise FileNotFoundError(source)
    version=tomllib.loads((extension/"blender_manifest.toml").read_text("utf-8"))["version"]
    target=extension/"bin"/FILENAME; target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,target); data=target.read_bytes()
    payload={"schema_version":1,"cloth_next_version":version,"filename":FILENAME,
             "platform":"windows-x64","file_size":len(data),
             "sha256":hashlib.sha256(data).hexdigest()}
    manifest=extension/"companion_manifest.json"
    manifest.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return target

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("source",type=Path); args=parser.parse_args()
    print(stage(args.source))
