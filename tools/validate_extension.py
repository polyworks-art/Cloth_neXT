"""Validate that Cloth NeXt is a Blender extension without a nested package root."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path, PurePosixPath
from zipfile import ZipFile


def validate_source_tree(root: Path) -> None:
    root = root.resolve()
    required = (root / "blender_manifest.toml", root / "__init__.py")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"Missing extension-root files: {missing}")
    manifest = tomllib.loads(required[0].read_text(encoding="utf-8"))
    extension_id = manifest.get("id")
    if not extension_id:
        raise ValueError("Manifest id is missing")
    if (root / extension_id / "blender_manifest.toml").exists():
        raise ValueError(f"Redundant nested extension root: {extension_id}/{extension_id}")


def validate_zip(archive: Path) -> None:
    with ZipFile(archive) as bundle:
        names = {PurePosixPath(name) for name in bundle.namelist() if not name.endswith("/")}
    if PurePosixPath("blender_manifest.toml") not in names or PurePosixPath("__init__.py") not in names:
        raise ValueError("ZIP must contain blender_manifest.toml and __init__.py at archive root")
    if any(len(name.parts) > 1 and name.parts[:2] == ("cloth_next", "cloth_next") for name in names):
        raise ValueError("ZIP contains a redundant cloth_next/cloth_next directory")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        if args.path.suffix.lower() == ".zip":
            validate_zip(args.path)
        else:
            validate_source_tree(args.path)
    except (OSError, ValueError) as exc:
        print(f"extension validation failed: {exc}", file=sys.stderr)
        return 1
    print("extension validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

