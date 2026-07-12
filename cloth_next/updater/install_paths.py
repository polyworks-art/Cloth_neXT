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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

VENDOR_DIRECTORY = "ClothNeXt"


@dataclass(frozen=True, slots=True)
class ActiveInstallation:
    version: str
    executable_relative: str
    activated_at: str

    def executable_path(self, paths: "ManagedSolverPaths") -> Path:
        version_dir = paths.version_dir(self.version)
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


def read_current(paths: ManagedSolverPaths) -> ActiveInstallation | None:
    """Load and strictly validate current.json; tampered metadata raises."""
    if not paths.current_json.is_file():
        return None
    try:
        payload = json.loads(paths.current_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"current.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("current.json must contain an object")
    version = payload.get("active_version")
    executable = payload.get("executable")
    if not isinstance(version, str) or not isinstance(executable, str):
        raise ValueError("current.json is malformed")
    paths.version_dir(version)  # raises on separators, '..', or empty versions
    executable = _validate_executable_relative(executable)
    return ActiveInstallation(version, executable,
                              str(payload.get("activated_at", "")))


def write_current(paths: ManagedSolverPaths, version: str,
                  executable_relative: str) -> ActiveInstallation:
    record = ActiveInstallation(version, executable_relative,
                                datetime.now(timezone.utc).isoformat())
    payload = {"active_version": record.version, "executable": record.executable_relative,
               "activated_at": record.activated_at}
    staged = paths.root / f".current-{uuid.uuid4().hex}.json"
    staged.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    staged.replace(paths.current_json)
    return record


def clear_current(paths: ManagedSolverPaths) -> None:
    paths.current_json.unlink(missing_ok=True)
