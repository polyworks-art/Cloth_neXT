#!/usr/bin/env python3
"""One-shot migration of selected GitHub Releases into the Pages artifact store."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/migrate-legacy-releases-to-pages.yml"
SCRIPT = Path(__file__).resolve()
DIAGNOSTICS = ROOT / "migration-diagnostics.txt"
TARGETS = ("2.0.0", "2.1.0")
REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "polyworks-art/Cloth_neXT")
BOT_NAME = "github-actions"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def run(*args: str, cwd: Path = ROOT, capture: bool = False) -> str:
    print("+", " ".join(args), flush=True)
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=None,
    )
    return completed.stdout.strip() if capture else ""


def gh_json(endpoint: str) -> object:
    return json.loads(run("gh", "api", endpoint, capture=True))


def tag_commit(tag: str) -> str:
    value = run("git", "rev-list", "-n", "1", tag, capture=True)
    if not value:
        raise RuntimeError(f"tag {tag!r} does not resolve to a commit")
    return value


def zip_member(bundle: zipfile.ZipFile, suffix: str) -> str:
    matches = [name for name in bundle.namelist() if name.endswith(suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"archive has ambiguous {suffix}: {matches}")
    return matches[0]


def protocols_from_solver_manifest(payload: dict) -> list[str]:
    protocols: set[str] = set()
    for platform in payload.get("platforms", {}).values():
        if not isinstance(platform, dict):
            continue
        releases = platform.get("releases")
        entries = releases if isinstance(releases, list) else [platform]
        for entry in entries:
            if isinstance(entry, dict) and entry.get("protocol_version"):
                protocols.add(str(entry["protocol_version"]))
    if not protocols:
        raise RuntimeError("solver compatibility metadata contains no protocol version")
    return sorted(protocols)


def validate_no_solver_material(names: list[str]) -> None:
    lowered = [name.lower().replace("\\", "/") for name in names]
    forbidden = [
        name
        for name in lowered
        if (
            "ppf-cts-server" in name
            or "ppf-contact-solver" in name
            or name.endswith("/headless.bat")
            or "/runtime/vendor.dll" in name
            or "/managed_solver/" in name
        )
    ]
    if forbidden:
        raise RuntimeError(f"forbidden solver material in legacy ZIP: {forbidden[:10]}")


def write_if_identical_or_new(path: Path, data: bytes) -> None:
    if path.exists() and path.read_bytes() != data:
        raise RuntimeError(f"existing canonical artifact differs: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def validate_existing_artifact(pages: Path, version: str) -> None:
    root = pages / "artifacts" / version
    archive = root / f"cloth_next-{version}-windows-x64.zip"
    manifest_path = root / "release-manifest.json"
    sums_path = root / "SHA256SUMS.txt"
    notes_path = root / "RELEASE_NOTES.md"
    for path in (archive, manifest_path, sums_path, notes_path):
        if not path.is_file():
            raise RuntimeError(f"canonical artifact is incomplete: {path}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("cloth_next_version") != version:
        raise RuntimeError(f"canonical manifest version mismatch for {version}")
    if manifest.get("extension_zip_sha256") != digest:
        raise RuntimeError(f"canonical ZIP hash mismatch for {version}")
    expected_line = f"{digest}  {archive.name}"
    if expected_line not in sums_path.read_text(encoding="utf-8").splitlines():
        raise RuntimeError(f"canonical checksum file mismatch for {version}")
    if manifest.get("git_commit") != tag_commit(str(manifest.get("git_tag"))):
        raise RuntimeError(f"canonical manifest tag mismatch for {version}")


def migrate_release(pages: Path, release: dict, version: str, temp: Path) -> None:
    tag = str(release["tag_name"])
    source = temp / version
    source.mkdir(parents=True, exist_ok=True)
    run("gh", "release", "download", tag, "--dir", str(source))

    candidates = [
        path
        for path in source.glob("*.zip")
        if "cloth" in path.name.lower() and "next" in path.name.lower()
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one Cloth NeXt ZIP for {version}, found {[p.name for p in candidates]}"
        )
    source_zip = candidates[0]
    zip_bytes = source_zip.read_bytes()

    with zipfile.ZipFile(source_zip) as bundle:
        names = bundle.namelist()
        validate_no_solver_material(names)
        manifest = tomllib.loads(
            bundle.read(zip_member(bundle, "blender_manifest.toml")).decode("utf-8")
        )
        if str(manifest.get("version")) != version:
            raise RuntimeError(
                f"ZIP manifest version {manifest.get('version')!r} does not match {version}"
            )
        solver = json.loads(bundle.read(zip_member(bundle, "solver_compatibility.json")))
        if not any(name.endswith("companion_manifest.json") for name in names):
            raise RuntimeError(f"legacy ZIP {version} has no companion manifest")
        if not any(name.endswith("bin/cloth-next-bake.exe") for name in names):
            raise RuntimeError(f"legacy ZIP {version} has no Cloth NeXt Bake companion")

    canonical_name = f"cloth_next-{version}-windows-x64.zip"
    digest = hashlib.sha256(zip_bytes).hexdigest()
    protocols = protocols_from_solver_manifest(solver)
    channel = "stable" if version == "2.0.0" else "beta"
    metadata = {
        "cloth_next_version": version,
        "git_tag": tag,
        "git_commit": tag_commit(tag),
        "release_channel": channel,
        "build_date": release.get("published_at") or release.get("created_at"),
        "blender_minimum_version": manifest.get("blender_version_min"),
        "platform": "windows-x64",
        "required_ppf_protocol": protocols[0] if len(protocols) == 1 else protocols,
        "solver_compatibility_manifest_version": solver.get("manifest_version"),
        "solver_bundled": False,
        "extension_zip_sha256": digest,
        "extension_zip_name": canonical_name,
    }

    body = str(release.get("body") or "").strip()
    if not body:
        body = f"# Cloth NeXt {version}\n\nLegacy GitHub release notes were empty."
    body += "\n"

    destination = pages / "artifacts" / version
    write_if_identical_or_new(destination / canonical_name, zip_bytes)
    write_if_identical_or_new(
        destination / "release-manifest.json",
        (json.dumps(metadata, indent=2) + "\n").encode("utf-8"),
    )
    write_if_identical_or_new(
        destination / "SHA256SUMS.txt",
        f"{digest}  {canonical_name}\n".encode("utf-8"),
    )
    write_if_identical_or_new(destination / "RELEASE_NOTES.md", body.encode("utf-8"))
    validate_existing_artifact(pages, version)


def commit_pages(pages: Path) -> None:
    run("git", "add", "artifacts/2.0.0", "artifacts/2.1.0", cwd=pages)
    changed = run("git", "diff", "--cached", "--name-only", cwd=pages, capture=True)
    paths = [line for line in changed.splitlines() if line]
    for path in paths:
        if not (path.startswith("artifacts/2.0.0/") or path.startswith("artifacts/2.1.0/")):
            raise RuntimeError(f"unexpected gh-pages path staged: {path}")
    if paths:
        run(
            "git",
            "-c",
            f"user.name={BOT_NAME}",
            "-c",
            f"user.email={BOT_EMAIL}",
            "commit",
            "-m",
            "Migrate 2.0.0 and 2.1.0 release artifacts",
            cwd=pages,
        )
        run("git", "push", "origin", "HEAD:gh-pages", cwd=pages)
    run("git", "fetch", "origin", "gh-pages")
    if run("git", "rev-parse", "HEAD", cwd=pages, capture=True) != run(
        "git", "rev-parse", "origin/gh-pages", capture=True
    ):
        raise RuntimeError("published gh-pages commit does not match the verified worktree")
    for version in TARGETS:
        validate_existing_artifact(pages, version)
        for name in (
            f"cloth_next-{version}-windows-x64.zip",
            "release-manifest.json",
            "SHA256SUMS.txt",
            "RELEASE_NOTES.md",
        ):
            run("git", "cat-file", "-e", f"origin/gh-pages:artifacts/{version}/{name}")


def delete_releases_and_verify_tags(releases: list[dict]) -> None:
    preserved: dict[str, str] = {}
    for release in releases:
        tag = str(release.get("tag_name") or "")
        if not tag:
            raise RuntimeError(f"release has no tag: {release}")
        preserved[tag] = tag_commit(tag)

    for tag in preserved:
        run("gh", "release", "delete", tag, "--yes")

    run("git", "fetch", "origin", "--tags", "--force")
    for tag, expected in preserved.items():
        if tag_commit(tag) != expected:
            raise RuntimeError(f"tag {tag} moved or disappeared during release deletion")

    remaining = gh_json(f"repos/{REPOSITORY}/releases?per_page=100")
    if remaining:
        raise RuntimeError(
            "public releases remain after cleanup: "
            + ", ".join(str(item.get("tag_name")) for item in remaining)
        )


def clean_one_shot_files() -> None:
    run("git", "pull", "--ff-only", "origin", "main")
    for path in (WORKFLOW, SCRIPT, DIAGNOSTICS):
        if path.exists():
            path.unlink()
    run("git", "add", "-A", str(WORKFLOW), str(SCRIPT), str(DIAGNOSTICS))
    changed = run("git", "diff", "--cached", "--name-only", capture=True)
    if changed:
        run(
            "git",
            "-c",
            f"user.name={BOT_NAME}",
            "-c",
            f"user.email={BOT_EMAIL}",
            "commit",
            "-m",
            "Remove legacy release migration tooling",
        )
        run("git", "push", "origin", "HEAD:main")


def main() -> int:
    releases_obj = gh_json(f"repos/{REPOSITORY}/releases?per_page=100")
    if not isinstance(releases_obj, list):
        raise RuntimeError("GitHub release inventory is not a list")
    releases: list[dict] = [item for item in releases_obj if isinstance(item, dict)]
    print("Public releases:", [item.get("tag_name") for item in releases], flush=True)

    run("git", "fetch", "origin", "--tags", "--force")
    run("git", "fetch", "origin", "gh-pages")

    by_version = {
        str(item.get("tag_name", "")).removeprefix("v"): item for item in releases
    }

    with tempfile.TemporaryDirectory(prefix="cloth-next-release-migration-") as directory:
        temp = Path(directory)
        pages = temp / "pages"
        run("git", "worktree", "add", str(pages), "origin/gh-pages")
        try:
            for version in TARGETS:
                release = by_version.get(version)
                if release is not None:
                    migrate_release(pages, release, version, temp / "downloads")
                else:
                    validate_existing_artifact(pages, version)
            commit_pages(pages)
        finally:
            run("git", "worktree", "remove", "--force", str(pages))

    if releases:
        delete_releases_and_verify_tags(releases)
    else:
        print("Release list is already empty; only final cleanup remains.", flush=True)

    clean_one_shot_files()
    print("Legacy release migration completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LEGACY RELEASE MIGRATION FAILED: {exc}", file=sys.stderr, flush=True)
        raise
