# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive an isolated Dev package version without changing source history."""
from __future__ import annotations
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path

PATTERN=re.compile(r"^0\.2\.0-dev\.([1-9]\d*)$")
def prepare(root: Path, version: str, commit: str, run_id: str) -> None:
    if not PATTERN.fullmatch(version): raise ValueError("expected 0.2.0-dev.N")
    manifest=root/"cloth_next/blender_manifest.toml"
    text=manifest.read_text(encoding="utf-8")
    text=re.sub(r'^version = "[^"]+"$',f'version = "{version}"',text,flags=re.M)
    manifest.write_text(text,encoding="utf-8")
    compat=root/"cloth_next/solver_compatibility.json"
    payload=json.loads(compat.read_text(encoding="utf-8")); payload["cloth_next_version"]=version
    compat.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    metadata={"dev_version":version,"source_commit":commit,"build_timestamp":datetime.now(timezone.utc).isoformat(),
              "workflow_run_id":run_id,"experimental":True,
              "checks_performed":["source imports","companion build/hash","Blender extension validation","artifact scan"],
              "checks_skipped":["real PPF integration","interactive Blender acceptance","full release preflight"]}
    (root/"cloth_next/dev_build.json").write_text(json.dumps(metadata,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--version",required=True);p.add_argument("--commit",required=True);p.add_argument("--run-id",required=True);p.add_argument("--root",type=Path,default=Path.cwd())
    a=p.parse_args();prepare(a.root,a.version,a.commit,a.run_id)
