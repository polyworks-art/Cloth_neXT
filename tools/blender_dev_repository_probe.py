# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side probe used only by the Dev repository regression harness."""

from __future__ import annotations

import importlib
import json
import sys
import tomllib
from pathlib import Path


def _args() -> dict:
    values = sys.argv[sys.argv.index("--") + 1:]
    return dict(zip(values[::2], values[1::2], strict=True))


def _candidate(index_path: Path) -> tuple[str, int]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    entries = [entry for entry in payload["data"]
               if entry.get("id") == "cloth_next"]
    if not entries:
        raise AssertionError("repository has no cloth_next candidate")
    versions = [entry["version"] for entry in entries]
    return max(versions, key=lambda value: int(value.rsplit(".", 1)[1])), len(entries)


def main() -> None:
    import bpy

    args = _args()
    expected = args["--expected"]
    index_path = Path(args["--index"])
    candidate, duplicate_count = _candidate(index_path)

    repos = bpy.context.preferences.extensions.repos
    for repo in list(repos):
        repo.enabled = False
    repo = repos.new(name="Cloth NeXt Dev Regression", module="cloth_next_dev_test",
                     remote_url=args["--url"], source="USER")
    repo.enabled = True
    bpy.ops.wm.save_userpref()
    bpy.ops.extensions.repo_sync(repo_directory=repo.directory)
    enabled = [item for item in repos if item.enabled and item.remote_url]
    repo_index = next(index for index, item in enumerate(enabled)
                      if item.directory == repo.directory)
    bpy.ops.extensions.package_install(repo_index=repo_index,
                                       pkg_id="cloth_next")

    manifest_path = Path(repo.directory) / "cloth_next" / "blender_manifest.toml"
    installed = tomllib.loads(manifest_path.read_text(encoding="utf-8"))["version"]
    module = importlib.import_module("bl_ext.cloth_next_dev_test.cloth_next")
    loaded = module.manifest_version()
    result = {
        "repository_candidate": candidate,
        "duplicate_count": duplicate_count,
        "installed_manifest": installed,
        "loaded_manifest": loaded,
        "update_offered": candidate != installed,
        "expected": expected,
        "repo_directory": repo.directory,
    }
    Path(args["--result"]).write_text(json.dumps(result, indent=2),
                                      encoding="utf-8")
    print("CLOTH_NEXT_DEV_PROBE=" + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
