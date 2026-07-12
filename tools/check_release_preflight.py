"""Require a successful unpublished preflight for an exact commit and version."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path


def candidate_version(path: Path) -> str:
    with zipfile.ZipFile(path) as bundle:
        return tomllib.loads(bundle.read("blender_manifest.toml").decode())["version"]


def matching_run(commit: str, version: str) -> int:
    result = subprocess.run(
        ["gh", "run", "list", "--workflow", "release-preflight.yml", "--limit", "100",
         "--json", "databaseId,headSha,conclusion"], check=True, capture_output=True,
        text=True, shell=False)
    for run in json.loads(result.stdout):
        if run["headSha"] != commit or run["conclusion"] != "success":
            continue
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["gh", "run", "download", str(run["databaseId"]),
                            "--name", "cloth-next-release-candidate", "--dir", directory],
                           check=True, shell=False)
            archives = list(Path(directory).glob("*.zip"))
            if len(archives) != 1:
                continue
            if candidate_version(archives[0]) == version:
                return int(run["databaseId"])
    raise RuntimeError(f"no successful release-preflight for commit {commit} and version {version}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(f"matching release-preflight run: {matching_run(args.commit, args.version)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
