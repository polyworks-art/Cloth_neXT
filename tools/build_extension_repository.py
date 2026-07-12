# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Assemble a channel directory and generate the Blender extension index.

The index.json is produced exclusively by the official Blender tooling
(``blender --command extension server-generate``); no custom schema is ever
invented. The channel ZIP must be byte-identical to the tested release asset,
verified by SHA-256.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloth_next.ppf.bootstrap import sha256_file


def assemble_channel(zip_path: Path, channel_dir: Path, expected_sha256: str) -> Path:
    actual = sha256_file(zip_path)
    if actual != expected_sha256.lower():
        raise ValueError(f"repository ZIP hash {actual} does not match the tested "
                         f"release artifact hash {expected_sha256}")
    channel_dir.mkdir(parents=True, exist_ok=True)
    destination = channel_dir / zip_path.name
    if destination.exists():
        if sha256_file(destination) != actual:
            raise ValueError(f"{destination.name} already exists in the channel with "
                             "different bytes; release artifacts are immutable")
        return destination
    shutil.copyfile(zip_path, destination)
    return destination


def generate_index(blender: str, channel_dir: Path) -> Path:
    subprocess.run(
        [blender, "--command", "extension", "server-generate",
         f"--repo-dir={channel_dir}"],
        check=True, shell=False)
    index = channel_dir / "index.json"
    if not index.is_file():
        raise ValueError("Blender did not generate index.json")
    return index


def validate_index(channel_dir: Path, extension_id: str, version: str) -> None:
    payload = json.loads((channel_dir / "index.json").read_text(encoding="utf-8"))
    entries = payload.get("data")
    if not isinstance(entries, list):
        raise ValueError("index.json has no data list")
    for entry in entries:
        if entry.get("id") == extension_id and entry.get("version") == version:
            return
    raise ValueError(f"index.json lists no entry for {extension_id} {version}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--sha256", required=True,
                        help="SHA-256 of the tested release artifact")
    parser.add_argument("--channel", required=True, choices=("stable", "beta"))
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--blender", default=os.environ.get("CLOTH_NEXT_BLENDER", "blender"))
    parser.add_argument("--repository-root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        manifest = tomllib.loads(
            (args.repository_root / "cloth_next" / "blender_manifest.toml")
            .read_text(encoding="utf-8"))
        channel_dir = args.site_dir / args.channel
        assemble_channel(args.zip, channel_dir, args.sha256)
        generate_index(args.blender, channel_dir)
        validate_index(channel_dir, manifest["id"], manifest["version"])
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(f"extension repository build failed: {exc}", file=sys.stderr)
        return 1
    print(f"extension repository ready: {channel_dir / 'index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
