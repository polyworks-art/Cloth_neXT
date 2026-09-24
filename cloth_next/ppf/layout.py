# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Immutable descriptions of bundled solver trees."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..platform_support import platform_spec

_HOST = platform_spec()
PLATFORM_DIRECTORY = Path("solver") / _HOST.solver_platform
EXECUTABLE_NAME = _HOST.solver_filename


@dataclass(frozen=True, slots=True)
class BundledSolverLayout:
    root_directory: Path
    executable_path: Path
    source_metadata_path: Path
    licenses_directory: Path
    platform: str = _HOST.os_name
    architecture: str = _HOST.architecture

    @classmethod
    def from_executable(cls, executable: Path) -> "BundledSolverLayout":
        path = executable.expanduser().resolve()
        parent = path.parent
        if (parent.name == "release" and parent.parent.name in ("cpu", "cuda", "rocm")
                and parent.parent.parent.name == "target"):
            return cls.from_root(parent.parent.parent.parent)
        if parent.name == "release" and parent.parent.name == "target":
            return cls.from_root(parent.parent.parent)
        return cls.from_root(parent)

    @classmethod
    def from_root(cls, root: Path) -> "BundledSolverLayout":
        normalized = root.expanduser().resolve()
        candidates = (normalized / EXECUTABLE_NAME,
                      normalized / "target" / "release" / EXECUTABLE_NAME,
                      normalized / "target" / "cpu" / "release" / EXECUTABLE_NAME)
        executable = next((path for path in candidates if path.is_file()), candidates[0])
        return cls(normalized, executable, normalized / "SOURCE.json", normalized / "LICENSES")

    @property
    def complete(self) -> bool:
        return (self.executable_path.is_file() and self.source_metadata_path.is_file()
                and self.licenses_directory.is_dir()
                and any(path.is_file() for path in self.licenses_directory.rglob("*")))

    def source_metadata(self) -> dict[str, Any]:
        value = json.loads(self.source_metadata_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("SOURCE.json must contain an object")
        return value

    def process_environment(self) -> tuple[tuple[str, str], ...]:
        """Environment additions for the official redistributable or dev layout."""
        root = self.root_directory
        python_dir = root / "python"
        bin_dir = root / "bin"
        entries = [str(path) for path in (python_dir, bin_dir) if path.is_dir()]
        current_path = os.environ.get("PATH", "")
        environment = {
            "PATH": os.pathsep.join(entries + ([current_path] if current_path else [])),
            "PYTHONPATH": str(root),
        }
        if self.platform == "linux":
            # Gaia's native Linux launcher selects the build worker's bundled
            # environment explicitly.  Do the same for a verified installation
            # instead of allowing the server to fall back to runner ``python3``.
            candidate = root / "python" / "bin" / "python3"
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root.resolve(strict=True))
            except (OSError, ValueError):
                pass
            else:
                if resolved.is_file():
                    environment["PPF_CTS_BUILD_PYTHON"] = str(resolved)
        return tuple(sorted(environment.items()))
