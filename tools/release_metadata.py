# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate release-manifest.json and SHA256SUMS.txt for a built extension ZIP.

The release manifest never claims that the PPF solver is included:
``solver_bundled`` is always ``false``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloth_next.ppf.bootstrap import sha256_file
from tools.validate_release_policy import RELEASE_PLATFORM, tag_to_version


def build_release_manifest(repository_root: Path, zip_path: Path, *,
                           tag: str, commit: str) -> dict[str, object]:
    version = tag_to_version(tag)
    manifest = tomllib.loads(
        (repository_root / "cloth_next" / "blender_manifest.toml")
        .read_text(encoding="utf-8"))
    solver = json.loads(
        (repository_root / "cloth_next" / "solver_compatibility.json")
        .read_text(encoding="utf-8"))
    protocols = {entry["protocol_version"]
                 for entry in solver.get("platforms", {}).values()}
    if len(protocols) != 1:
        raise ValueError("solver_compatibility.json must pin exactly one protocol version")
    return {
        "cloth_next_version": version.text,
        "git_tag": tag,
        "git_commit": commit,
        "release_channel": version.channel,
        "build_date": datetime.now(timezone.utc).isoformat(),
        "blender_minimum_version": manifest["blender_version_min"],
        "platform": RELEASE_PLATFORM,
        "required_ppf_protocol": protocols.pop(),
        "solver_compatibility_manifest_version": solver["manifest_version"],
        "solver_bundled": False,
        "extension_zip_sha256": sha256_file(zip_path),
        "extension_zip_name": zip_path.name,
    }


def write_metadata(repository_root: Path, zip_path: Path, output_dir: Path, *,
                   tag: str, commit: str) -> tuple[Path, Path]:
    payload = build_release_manifest(repository_root, zip_path, tag=tag, commit=commit)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    sums_path = output_dir / "SHA256SUMS.txt"
    sums_path.write_text(f"{payload['extension_zip_sha256']}  {zip_path.name}\n",
                         encoding="utf-8")
    return manifest_path, sums_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--repository-root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        manifest_path, sums_path = write_metadata(
            args.repository_root, args.zip, args.output_dir,
            tag=args.tag, commit=args.commit)
    except (OSError, ValueError, KeyError) as exc:
        print(f"release metadata generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {manifest_path} and {sums_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
