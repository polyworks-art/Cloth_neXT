# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Validate that Cloth NeXt is a Blender extension without a nested package root."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path, PurePosixPath
import hashlib
from zipfile import ZipFile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloth_next.platform_support import spec_for_blender_platform


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
    if ((root / "bin" / "cloth-next-bake.exe").exists()
            or (root / "bin" / "cloth-next-bake").exists()
            or (root / "companion_manifest.json").exists()):
        raise ValueError("source phase must not contain generated companion artifacts")
    if ((root / "tethys_manifest.json").exists()
            or any((root / "bin" / name).exists() for name in TETHYS_FILENAMES.values())):
        raise ValueError("source phase must not contain a staged TETHYS Engine")
    if (root / "aether_engine_manifest.json").exists() or (root / "bin" / "AETHER Engine.exe").exists():
        raise ValueError("source phase must not contain a staged AETHER Engine")


TETHYS_FILENAMES = {"windows-x64": "TETHYS Engine.exe", "linux-x64": "tethys-engine"}
AETHER_FILENAMES = {"windows-x64": "AETHER Engine.exe"}
_ENGINE_ENTRIES = ({PurePosixPath(f"bin/{name}") for name in TETHYS_FILENAMES.values()}
                   | {PurePosixPath(f"bin/{name}") for name in AETHER_FILENAMES.values()}
                   | {PurePosixPath("tethys_manifest.json"), PurePosixPath("aether_engine_manifest.json")})


def _reject_bundled_engines(names) -> None:
    """AETHER and TETHYS are installed from the PolyWorks server, never shipped in the ZIP."""
    bundled = sorted(str(name) for name in _ENGINE_ENTRIES & names)
    if bundled:
        raise ValueError("packaged extension must not bundle a native engine (installed from the "
                         f"server instead): {', '.join(bundled)}")


def validate_zip(archive: Path) -> None:
    with ZipFile(archive) as bundle:
        names = {PurePosixPath(name) for name in bundle.namelist() if not name.endswith("/")}
        if PurePosixPath("companion_manifest.json") not in names:
            raise ValueError("packaged extension is missing the companion manifest")
        manifest_payload = json.loads(
            bundle.read("companion_manifest.json").decode("utf-8"))
        selected = spec_for_blender_platform(manifest_payload.get("platform", ""))
        companion_path = PurePosixPath(f"bin/{selected.companion_filename}")
        if companion_path in names:
            companion_info = bundle.getinfo(companion_path.as_posix())
            if (selected.executable_permission_required
                    and not ((companion_info.external_attr >> 16) & 0o111)):
                raise ValueError("packaged Linux companion is missing executable mode")
    if PurePosixPath("blender_manifest.toml") not in names or PurePosixPath("__init__.py") not in names:
        raise ValueError("ZIP must contain blender_manifest.toml and __init__.py at archive root")
    if any(len(name.parts) > 1 and name.parts[:2] == ("cloth_next", "cloth_next") for name in names):
        raise ValueError("ZIP contains a redundant cloth_next/cloth_next directory")
    required = {PurePosixPath(f"bin/{selected.companion_filename}"),
                PurePosixPath("companion_manifest.json")}
    if not required <= names:
        raise ValueError("packaged extension is missing the staged companion or its manifest")
    foreign = {PurePosixPath("bin/cloth-next-bake.exe"),
               PurePosixPath("bin/cloth-next-bake")} - required
    if names & foreign:
        raise ValueError("packaged extension contains a foreign-platform companion")
    with ZipFile(archive) as bundle:
        _reject_bundled_engines(names)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--phase", required=True, choices=("source", "packaged"))
    args = parser.parse_args()
    try:
        if args.phase == "packaged":
            if args.path.suffix.lower() != ".zip":
                raise ValueError("packaged phase requires an explicit ZIP path")
            validate_zip(args.path)
        else:
            if args.path.suffix.lower() == ".zip":
                raise ValueError("source phase requires an extension source directory")
            validate_source_tree(args.path)
    except (OSError, ValueError) as exc:
        print(f"extension validation failed: {exc}", file=sys.stderr)
        return 1
    print("extension validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

