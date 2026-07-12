"""Secure local import primitives used only by the explicit bootstrap tool."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .compatibility import EXPECTED_PROTOCOL, EXPECTED_SCHEMA
from .layout import BundledSolverLayout, EXECUTABLE_NAME

UPSTREAM_BASELINE = "7193f158e3843597070f66cb29af19efd9bdcff7"


def safe_extract_zip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/"))
            if name.is_absolute() or ".." in name.parts:
                raise ValueError(f"unsafe archive member: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"symbolic links are not allowed: {info.filename}")
            destination = target.joinpath(*name.parts)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def copy_directory(source: Path, target: Path) -> None:
    if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("solver imports may not contain symbolic links")
    shutil.copytree(source, target)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_single_executable(root: Path) -> Path:
    found = list(root.rglob(EXECUTABLE_NAME))
    if len(found) != 1:
        raise ValueError(f"expected exactly one {EXECUTABLE_NAME}, found {len(found)}")
    return found[0]


def find_license_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()
            and (path.name.upper().startswith("LICENSE") or path.name.upper().startswith("NOTICE"))]


def normalize_bundle_root(staging: Path) -> Path:
    executable = find_single_executable(staging)
    if executable.parent.name == "release" and executable.parent.parent.name == "target":
        return staging
    return executable.parent


def write_source_metadata(layout: BundledSolverLayout, *, source_type: str,
                          source_url: str | None, source_label: str,
                          versions: tuple[str, str, str], health_passed: bool,
                          upstream_commit: str) -> None:
    package, protocol, schema = versions
    files = {}
    for path in layout.root_directory.rglob("*"):
        if path.is_file() and path.name != "SOURCE.json":
            relative = path.relative_to(layout.root_directory).as_posix()
            files[relative] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    payload = {
        "source_type": source_type, "source_url": source_url,
        "source_path": source_label, "installed_at": datetime.now(timezone.utc).isoformat(),
        "package_version": package, "protocol_version": protocol,
        "schema_version": schema, "upstream_commit": upstream_commit,
        "platform": "windows", "architecture": "x86_64",
        "health_check": "passed" if health_passed else "not_run", "files": files,
    }
    layout.source_metadata_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def atomic_replace_directory(staged: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.parent / f".backup-{uuid.uuid4().hex}"
    try:
        if target.exists():
            target.replace(backup)
        staged.replace(target)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if target.exists() and backup.exists():
            shutil.rmtree(target)
        if backup.exists():
            backup.replace(target)
        raise

