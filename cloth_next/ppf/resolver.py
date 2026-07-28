# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure priority resolver for managed, external, and development solvers.

The extension never bundles a solver and the repository tree is never scanned
implicitly: the only sources are a user-selected external installation, the
managed installation created by the separate installer, an explicitly
configured development executable (``CLOTH_NEXT_PPF_EXECUTABLE``), or an
already-running external server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from .layout import BundledSolverLayout
from .models import ConnectionOwnership
from .compatibility import ProtocolProfile, protocol_profile
from ..updater.solver_registry import SolverInstallation

DEVELOPMENT_EXECUTABLE_ENV = "CLOTH_NEXT_PPF_EXECUTABLE"


class SolverMode(Enum):
    MANAGED_INSTALLATION = auto()
    EXTERNAL_INSTALLATION = auto()
    EXTERNAL_SERVER = auto()
    DEVELOPMENT = auto()


@dataclass(frozen=True, slots=True)
class SolverResolutionContext:
    external_path: Path | None = None
    managed_root: Path | None = None
    development_executable: Path | None = None
    external_server_available: bool = False
    selected_installation: SolverInstallation | None = None


@dataclass(frozen=True, slots=True)
class ResolvedSolver:
    mode: SolverMode
    root_directory: Path | None
    executable_path: Path | None
    package_version: str | None
    protocol_version: str | None
    schema_version: str | None
    ownership: ConnectionOwnership
    source_metadata: dict[str, object] | None
    writable: bool
    installation: SolverInstallation | None = None
    protocol_profile: ProtocolProfile | None = None
    frontend_path: Path | None = None

    @property
    def installation_id(self) -> str | None:
        return (self.installation.installation_id
                if self.installation is not None else None)


def development_executable_from_environment() -> Path | None:
    value = os.environ.get(DEVELOPMENT_EXECUTABLE_ENV, "").strip()
    return Path(value) if value else None


class SolverResolver:
    def __init__(self, version_probe: Callable[[Path], tuple[str, str, str]]) -> None:
        self._version_probe = version_probe

    def _local(self, mode: SolverMode, layout: BundledSolverLayout,
               writable: bool) -> ResolvedSolver | None:
        if not layout.executable_path.is_file():
            return None
        package, protocol, schema = self._version_probe(layout.executable_path)
        metadata = layout.source_metadata() if layout.source_metadata_path.is_file() else None
        return ResolvedSolver(mode, layout.root_directory, layout.executable_path, package,
            protocol, schema, ConnectionOwnership.OWNED_PROCESS, metadata, writable)

    def resolve(self, context: SolverResolutionContext) -> ResolvedSolver | None:
        if context.selected_installation is not None:
            selected = context.selected_installation
            if not (selected.available and selected.verified
                    and selected.healthy and selected.compatible):
                return None
            profile = protocol_profile(
                selected.protocol_version or "", selected.schema_version or "")
            if profile is None:
                return None
            mode = (SolverMode.MANAGED_INSTALLATION
                    if selected.managed else SolverMode.EXTERNAL_INSTALLATION)
            return ResolvedSolver(
                mode, selected.root, selected.executable,
                selected.package_version, selected.protocol_version,
                selected.schema_version, ConnectionOwnership.OWNED_PROCESS,
                None, selected.managed, selected, profile, selected.frontend)
        if context.external_path is not None:
            path = context.external_path.expanduser().resolve()
            root = path.parent if path.is_file() else path
            found = self._local(SolverMode.EXTERNAL_INSTALLATION,
                                BundledSolverLayout.from_root(root), False)
            if found:
                return found
        if context.managed_root is not None:
            found = self._local(SolverMode.MANAGED_INSTALLATION,
                                BundledSolverLayout.from_root(context.managed_root), True)
            if found:
                return found
        if context.development_executable is not None:
            executable = context.development_executable.expanduser().resolve()
            found = self._local(SolverMode.DEVELOPMENT,
                                BundledSolverLayout.from_root(executable.parent), True)
            if found:
                return found
        if context.external_server_available:
            return ResolvedSolver(SolverMode.EXTERNAL_SERVER, None, None, None, None, None,
                ConnectionOwnership.EXTERNAL_SERVER, None, False)
        return None
