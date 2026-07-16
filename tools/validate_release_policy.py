# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Automated enforcement of docs/RELEASE_POLICY.md.

Run before the build (``--phase pre-build``), after the build
(``--phase post-build``), and before publication (``--phase pre-publish``).
Any violation exits non-zero and must abort the release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloth_next.ppf.bootstrap import sha256_file
from cloth_next.updater.solver_manifest import parse_manifest
from tools.scan_release_artifact import scan_names
from cloth_next.bake.companion_bundle import validate_bundle

RELEASE_PLATFORM = "windows-x64"
SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<kind>beta|rc)\.(?P<pre>0|[1-9]\d*))?$")


@dataclass(frozen=True, slots=True)
class ReleaseVersion:
    text: str
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, int] | None

    @property
    def channel(self) -> str:
        if self.prerelease:
            return "beta"  # legacy tagged release compatibility
        if self.patch > 0:
            return "dev"
        if self.minor > 0:
            return "beta"
        if self.major > 0:
            return "stable"
        raise ValueError("0.0.0 does not encode a release channel")


def parse_version(text: str) -> ReleaseVersion:
    match = SEMVER_RE.match(text)
    if not match:
        raise ValueError(
            f"invalid version {text!r}: expected MAJOR.MINOR.PATCH with an optional "
            "-beta.N or -rc.N prerelease")
    prerelease = ((match["kind"], int(match["pre"])) if match["kind"] else None)
    return ReleaseVersion(text, int(match["major"]), int(match["minor"]),
                          int(match["patch"]), prerelease)


def tag_to_version(tag: str) -> ReleaseVersion:
    if not tag.startswith("v"):
        raise ValueError(f"release tags must look like v<version>, got {tag!r}")
    return parse_version(tag[1:])


def check_channel(version: ReleaseVersion, channel: str) -> None:
    if channel not in ("stable", "beta"):
        raise ValueError(f"unknown release channel {channel!r}")
    if version.channel != channel:
        raise ValueError(f"{version.text} encodes channel {version.channel}, not {channel}")


def expected_zip_name(version: ReleaseVersion) -> str:
    return f"cloth_next-{version.text}-{RELEASE_PLATFORM}.zip"


def read_manifest_version(repository_root: Path) -> str:
    manifest = tomllib.loads(
        (repository_root / "cloth_next" / "blender_manifest.toml")
        .read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("blender_manifest.toml has no version")
    return version


def check_tag_matches_manifest(tag: str, repository_root: Path) -> ReleaseVersion:
    version = tag_to_version(tag)
    manifest_version = read_manifest_version(repository_root)
    if version.text != manifest_version:
        raise ValueError(f"tag {tag} does not match manifest version {manifest_version}")
    return version


def check_solver_manifest(repository_root: Path) -> None:
    payload = json.loads(
        (repository_root / "cloth_next" / "solver_compatibility.json")
        .read_text(encoding="utf-8"))
    parse_manifest(payload,
                   expected_cloth_next_version=read_manifest_version(repository_root))


def check_zip(zip_path: Path, version: ReleaseVersion) -> None:
    expected = expected_zip_name(version)
    if zip_path.name != expected:
        raise ValueError(f"ZIP name {zip_path.name!r} must be {expected!r}")
    with zipfile.ZipFile(zip_path) as bundle:
        names = bundle.namelist()
        if "dev_build.json" in names:
            raise ValueError(
                "Beta/stable extension ZIP must never contain Dev build metadata "
                "or enable Developer Tools")
        violations = scan_names(names)
        if violations:
            raise ValueError("extension ZIP contains forbidden solver material: "
                             + "; ".join(violations))
        if "blender_manifest.toml" not in names:
            raise ValueError("extension ZIP misses blender_manifest.toml")
        manifest = tomllib.loads(
            bundle.read("blender_manifest.toml").decode("utf-8"))
        if manifest.get("version") != version.text:
            raise ValueError(
                f"manifest version inside the ZIP is {manifest.get('version')!r}, "
                f"expected {version.text!r}")
        if "solver_compatibility.json" not in names:
            raise ValueError("extension ZIP misses solver_compatibility.json")
        solver_manifest = json.loads(bundle.read("solver_compatibility.json"))
        parse_manifest(solver_manifest, expected_cloth_next_version=version.text)
        if "bin/cloth-next-bake.exe" not in names or "companion_manifest.json" not in names:
            raise ValueError("extension ZIP misses the approved bundled companion")
        companion = json.loads(bundle.read("companion_manifest.json"))
        binary = bundle.read("bin/cloth-next-bake.exe")
        if companion.get("cloth_next_version") != version.text:
            raise ValueError("companion manifest version mismatch")
        if companion.get("filename") != "cloth-next-bake.exe" or companion.get("platform") != "windows-x64":
            raise ValueError("invalid bundled companion identity")
        if companion.get("file_size") != len(binary) or companion.get("sha256") != __import__("hashlib").sha256(binary).hexdigest():
            raise ValueError("bundled companion size/hash mismatch")


def check_release_manifest(path: Path, zip_path: Path, version: ReleaseVersion,
                           tag: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("cloth_next_version") != version.text:
        raise ValueError("release-manifest.json cloth_next_version mismatch")
    if payload.get("git_tag") != tag:
        raise ValueError("release-manifest.json git_tag mismatch")
    if payload.get("release_channel") != version.channel:
        raise ValueError("release-manifest.json release_channel mismatch")
    if payload.get("solver_bundled") is not False:
        raise ValueError("release-manifest.json must declare solver_bundled = false")
    if payload.get("extension_zip_name") != zip_path.name:
        raise ValueError("release-manifest.json extension_zip_name mismatch")
    actual_hash = sha256_file(zip_path)
    if payload.get("extension_zip_sha256") != actual_hash:
        raise ValueError("release-manifest.json extension_zip_sha256 does not match "
                         "the actual ZIP")


def check_sha256sums(path: Path, zip_path: Path) -> None:
    expected = sha256_file(zip_path)
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == zip_path.name:
            if parts[0] != expected:
                raise ValueError(f"SHA256SUMS.txt hash mismatch for {zip_path.name}")
            return
    raise ValueError(f"SHA256SUMS.txt has no entry for {zip_path.name}")


def check_channel_separation(site_dir: Path, version: ReleaseVersion) -> None:
    """Only versions encoding the stable channel may appear in stable."""
    stable_dir = site_dir / "stable"
    if not stable_dir.is_dir():
        return
    for entry in stable_dir.glob("cloth_next-*.zip"):
        name_version = entry.name.removeprefix("cloth_next-").removesuffix(
            f"-{RELEASE_PLATFORM}.zip")
        archived = parse_version(name_version)
        if archived.channel != "stable":
            raise ValueError(
                f"non-stable artifact {entry.name} found in the stable channel")
    index = stable_dir / "index.json"
    if index.is_file():
        payload = json.loads(index.read_text(encoding="utf-8"))
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise ValueError("stable index.json has no package list")
        for entry in entries:
            if (isinstance(entry, dict) and entry.get("id") == "cloth_next"
                    and parse_version(str(entry.get("version"))).channel
                    != "stable"):
                raise ValueError(
                    "stable index.json references a non-stable version")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True,
                        choices=("pre-build", "post-build", "pre-publish"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository-root", type=Path,
                        default=Path(__file__).resolve().parents[1])
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--release-manifest", type=Path)
    parser.add_argument("--sha256sums", type=Path)
    parser.add_argument("--site-dir", type=Path)
    parser.add_argument("--print-channel", action="store_true")
    args = parser.parse_args()
    try:
        version = check_tag_matches_manifest(args.tag, args.repository_root)
        check_channel(version, version.channel)
        check_solver_manifest(args.repository_root)
        if args.phase in ("post-build", "pre-publish"):
            if args.zip is None:
                raise ValueError(f"--zip is required for phase {args.phase}")
            check_zip(args.zip, version)
        if args.phase == "pre-publish":
            if args.release_manifest is None or args.sha256sums is None:
                raise ValueError("--release-manifest and --sha256sums are required "
                                 "for phase pre-publish")
            check_release_manifest(args.release_manifest, args.zip, version, args.tag)
            check_sha256sums(args.sha256sums, args.zip)
            if args.site_dir is not None:
                check_channel_separation(args.site_dir, version)
    except (OSError, ValueError) as exc:
        print(f"RELEASE POLICY VIOLATION ({args.phase}): {exc}", file=sys.stderr)
        return 1
    if args.print_channel:
        print(version.channel)
    else:
        print(f"release policy validation passed ({args.phase}, "
              f"{version.text}, channel {version.channel})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
