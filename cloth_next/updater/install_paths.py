# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Managed solver installation layout outside the Blender extension tree.

The add-on and the external solver have separate lifecycles: add-on updates must
never delete the solver, extension directories may be read-only, and running
executables can be locked on Windows. The managed root therefore lives in a
user-writable per-user directory, never in the extension root, the Cloth NeXt
repository, Program Files, the current working directory, or a temp directory.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

VENDOR_DIRECTORY = "ClothNeXt"

#: current.json format written by this Cloth NeXt version. Version 1 is the
#: legacy format ({"active_version", "executable", "activated_at"}) that only
#: recorded the internal solver package version; it stays readable and
#: startable, but carries no official release identity.
LEGACY_METADATA_VERSION = 1
CURRENT_METADATA_VERSION = 2

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_INSTALLATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_installation_id(value: str) -> str:
    """Strictly validate a managed installation directory name.

    The installation id is derived from the immutable official release tag and
    becomes a directory name under ``versions/``; it must never allow path
    traversal, separators, or drive references.
    """
    name = value.strip()
    if (not name or "/" in name or "\\" in name or ".." in name
            or not _INSTALLATION_ID_RE.match(name)):
        raise ValueError(f"invalid managed installation id {value!r}")
    return name


@dataclass(frozen=True, slots=True)
class ActiveInstallation:
    """Identity of the active managed installation.

    ``installation_id`` names the directory under ``versions/``. For metadata
    version 2 it equals the immutable official release tag; legacy metadata
    only knew the internal solver package version, so there the id is that
    package version and the official release identity is unknown (``None``).
    """

    metadata_version: int
    installation_id: str
    solver_package_version: str
    executable_relative: str
    activated_at: str
    official_release_tag: str | None = None
    official_asset_name: str | None = None
    asset_sha256: str | None = None

    @property
    def version(self) -> str:
        """Internal solver package version (display/compatibility info only)."""
        return self.solver_package_version

    @property
    def has_release_identity(self) -> bool:
        return bool(self.official_release_tag and self.asset_sha256)

    @property
    def release_label(self) -> str:
        """Human-readable installed release for the preferences UI."""
        if self.official_release_tag:
            return self.official_release_tag
        return f"Legacy installation (package {self.solver_package_version})"

    def executable_path(self, paths: "ManagedSolverPaths") -> Path:
        version_dir = paths.version_dir(self.installation_id)
        candidate = (version_dir / self.executable_relative).resolve()
        resolved_version_dir = version_dir.resolve()
        if (resolved_version_dir != candidate
                and resolved_version_dir not in candidate.parents):
            raise ValueError("current.json executable escapes the managed version "
                             "directory; the metadata was tampered with")
        return candidate


_EXECUTABLE_NAME = "ppf-cts-server.exe"


def _validate_executable_relative(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (not normalized or normalized.startswith("/") or ".." in parts
            or (parts and ":" in parts[0]) or parts[-1] != _EXECUTABLE_NAME):
        raise ValueError(f"current.json names an invalid executable {value!r}")
    return normalized


@dataclass(frozen=True, slots=True)
class ManagedSolverPaths:
    root: Path

    @classmethod
    def default(cls) -> "ManagedSolverPaths":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / ".local" / "share")
        return cls((Path(base) / VENDOR_DIRECTORY / "solver").resolve())

    @property
    def versions_dir(self) -> Path:
        return self.root / "versions"

    @property
    def downloads_dir(self) -> Path:
        return self.root / "downloads"

    @property
    def staging_dir(self) -> Path:
        return self.root / "staging"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def current_json(self) -> Path:
        return self.root / "current.json"

    def version_dir(self, version: str) -> Path:
        name = version.strip()
        if not name or any(sep in name for sep in ("/", "\\", "..")):
            raise ValueError(f"invalid managed solver version {version!r}")
        return self.versions_dir / name

    def ensure_layout(self) -> None:
        for directory in (self.versions_dir, self.downloads_dir,
                          self.staging_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def validate_outside(self, forbidden_roots: Iterable[Path | None]) -> None:
        root = self.root.resolve()
        for forbidden in forbidden_roots:
            if forbidden is None:
                continue
            candidate = Path(forbidden).resolve()
            if root == candidate or candidate in root.parents:
                raise ValueError(
                    f"managed solver root {root} must live outside {candidate} "
                    "(extension, repository, and working directories are forbidden)")


def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"current.json field {key!r} is missing or malformed")
    return value.strip()


def _parse_legacy(paths: ManagedSolverPaths, payload: dict) -> ActiveInstallation:
    """Legacy format: package version doubles as the installation directory.

    The exact official release identity is unknown; such installations stay
    startable but are offered the current manifest-pinned release as an
    update. The metadata is never rewritten just by reading it.
    """
    version = payload.get("active_version")
    executable = payload.get("executable")
    if not isinstance(version, str) or not isinstance(executable, str):
        raise ValueError("current.json is malformed")
    paths.version_dir(version)  # raises on separators, '..', or empty versions
    executable = _validate_executable_relative(executable)
    return ActiveInstallation(
        metadata_version=LEGACY_METADATA_VERSION, installation_id=version,
        solver_package_version=version, executable_relative=executable,
        activated_at=str(payload.get("activated_at", "")))


def _parse_v2(paths: ManagedSolverPaths, payload: dict) -> ActiveInstallation:
    installation_id = validate_installation_id(
        _require_str(payload, "installation_id"))
    paths.version_dir(installation_id)
    tag = _require_str(payload, "official_release_tag")
    sha256 = _require_str(payload, "asset_sha256").lower()
    if not _SHA256_RE.match(sha256):
        raise ValueError("current.json asset_sha256 must be 64 lowercase hex "
                         "characters")
    return ActiveInstallation(
        metadata_version=CURRENT_METADATA_VERSION,
        installation_id=installation_id,
        solver_package_version=_require_str(payload, "solver_package_version"),
        executable_relative=_validate_executable_relative(
            _require_str(payload, "executable")),
        activated_at=str(payload.get("activated_at", "")),
        official_release_tag=tag,
        official_asset_name=_require_str(payload, "official_asset_name"),
        asset_sha256=sha256)


def read_current(paths: ManagedSolverPaths) -> ActiveInstallation | None:
    """Load and strictly validate current.json; tampered metadata raises.

    Both the legacy version-1 format and the version-2 release-identity
    format are readable; unknown metadata versions are rejected so damaged or
    future metadata always leads into the repair flow instead of being
    trusted blindly.
    """
    if not paths.current_json.is_file():
        return None
    try:
        payload = json.loads(paths.current_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"current.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("current.json must contain an object")
    metadata_version = payload.get("metadata_version", LEGACY_METADATA_VERSION)
    if metadata_version == LEGACY_METADATA_VERSION:
        return _parse_legacy(paths, payload)
    if metadata_version == CURRENT_METADATA_VERSION:
        return _parse_v2(paths, payload)
    raise ValueError(f"current.json has unsupported metadata_version "
                     f"{metadata_version!r}")


def make_current_record(*, installation_id: str, solver_package_version: str,
                        executable_relative: str, official_release_tag: str,
                        official_asset_name: str,
                        asset_sha256: str) -> ActiveInstallation:
    """Build a fresh metadata-version-2 record with the activation timestamp."""
    return ActiveInstallation(
        metadata_version=CURRENT_METADATA_VERSION,
        installation_id=validate_installation_id(installation_id),
        solver_package_version=solver_package_version,
        executable_relative=executable_relative,
        activated_at=datetime.now(timezone.utc).isoformat(),
        official_release_tag=official_release_tag,
        official_asset_name=official_asset_name,
        asset_sha256=asset_sha256)


def write_current(paths: ManagedSolverPaths,
                  record: ActiveInstallation) -> ActiveInstallation:
    """Atomically persist ``record``, preserving its metadata format.

    Legacy records are written back in the legacy shape so a failed update
    can restore the previous installation without silently migrating it; the
    version-2 format is only written after a successful installation.
    """
    if record.metadata_version == LEGACY_METADATA_VERSION:
        payload = {"active_version": record.solver_package_version,
                   "executable": record.executable_relative,
                   "activated_at": record.activated_at}
    else:
        payload = {"metadata_version": CURRENT_METADATA_VERSION,
                   "installation_id": record.installation_id,
                   "official_release_tag": record.official_release_tag,
                   "official_asset_name": record.official_asset_name,
                   "asset_sha256": record.asset_sha256,
                   "solver_package_version": record.solver_package_version,
                   "executable": record.executable_relative,
                   "activated_at": record.activated_at}
    staged = paths.root / f".current-{uuid.uuid4().hex}.json"
    staged.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    staged.replace(paths.current_json)
    return record


def write_legacy_current(paths: ManagedSolverPaths, version: str,
                         executable_relative: str) -> ActiveInstallation:
    """Write a legacy (metadata version 1) record; used by tests and only
    ever produced by pre-identity Cloth NeXt versions."""
    record = ActiveInstallation(
        metadata_version=LEGACY_METADATA_VERSION, installation_id=version,
        solver_package_version=version, executable_relative=executable_relative,
        activated_at=datetime.now(timezone.utc).isoformat())
    return write_current(paths, record)


def clear_current(paths: ManagedSolverPaths) -> None:
    paths.current_json.unlink(missing_ok=True)
