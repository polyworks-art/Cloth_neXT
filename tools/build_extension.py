# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build the solver-free Cloth NeXt extension ZIP.

Cloth NeXt never repackages or redistributes the external PPF Contact Solver
(docs/RELEASE_POLICY.md section 6). Release builds go through the official
Blender extension tooling (``--blender``); the pure-Python fallback exists only
for development machines without Blender. Every build is scanned for forbidden
solver material and fails on any hit.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
import zipfile
import uuid
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.scan_release_artifact import scan_zip
from tools.validate_extension import validate_zip
from tools.build_icons import build as build_icons, validate as validate_icons
from cloth_next.platform_support import PlatformSpec, platform_spec

_EXCLUDED_DIRECTORIES = frozenset({"__pycache__", ".state", "solver", "downloads",
                                   "managed_solver", "staging", "logs"})


def default_output(source_root: Path, platform: PlatformSpec | None = None) -> Path:
    manifest = tomllib.loads((source_root / "blender_manifest.toml")
                             .read_text(encoding="utf-8"))
    selected = platform or platform_spec()
    return Path("dist") / f"cloth_next-{manifest['version']}-{selected.blender_platform}.zip"


def build_extension(source_root: Path, output: Path, *,
                    blender: str | None = None) -> Path:
    if (source_root / "blender").is_dir():
        build_icons()
        validate_icons()
    source_root = source_root.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if blender:
        subprocess.run(
            [blender, "--factory-startup", "--command", "extension", "build",
             f"--source-dir={source_root}", f"--output-filepath={output.resolve()}"],
            check=True, shell=False)
    else:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6) as bundle:
            for path in sorted(source_root.rglob("*")):
                relative = path.relative_to(source_root)
                if not path.is_file() or _EXCLUDED_DIRECTORIES & set(relative.parts):
                    continue
                bundle.write(path, relative)
    _prune_historical_whats_new(output)
    validate_zip(output)
    violations = scan_zip(output)
    if violations:
        output.unlink(missing_ok=True)
        raise ValueError("build aborted, forbidden solver material detected: "
                         + "; ".join(violations))
    return output


def _prune_historical_whats_new(archive: Path) -> None:
    """Ship only this candidate's release content, preserving ZIP modes."""
    temporary = archive.with_name(f".{archive.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(archive, "r") as source:
            manifest = tomllib.loads(
                source.read("blender_manifest.toml").decode("utf-8"))
            current = (
                f"resources/onboarding/whats_new/{manifest['version']}.json")
            with zipfile.ZipFile(
                    temporary, "w", compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6) as target:
                for info in source.infolist():
                    if (info.filename.startswith(
                            "resources/onboarding/whats_new/")
                            and info.filename.endswith(".json")
                            and info.filename != current):
                        continue
                    target.writestr(info, source.read(info.filename))
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("cloth_next"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--blender",
                        help="Blender executable for the official extension tooling")
    args = parser.parse_args()
    try:
        output = args.output or default_output(args.source_root)
        result = build_extension(args.source_root, output, blender=args.blender)
        print(f"Extension package: valid ({result.resolve()})")
        print("Solver: not bundled (Cloth NeXt never distributes the PPF solver)")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
