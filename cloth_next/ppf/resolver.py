"""Pure priority resolver for local and external PPF deployments."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable

from .compatibility import parse_executable_version
from .layout import BundledSolverLayout, PLATFORM_DIRECTORY
from .models import ConnectionOwnership


class SolverMode(Enum):
    REPOSITORY_BUNDLED = auto()
    EXTENSION_BUNDLED = auto()
    MANAGED_INSTALLATION = auto()
    EXTERNAL_INSTALLATION = auto()
    EXTERNAL_SERVER = auto()


@dataclass(frozen=True, slots=True)
class SolverResolutionContext:
    extension_root: Path
    repository_root: Path | None = None
    external_path: Path | None = None
    external_server_available: bool = False
    managed_root: Path | None = None


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


def extension_root() -> Path:
    return Path(__file__).resolve().parents[1]


def repository_root(candidate: Path | None = None) -> Path | None:
    root = (candidate or extension_root().parent).resolve()
    if ((root / "pyproject.toml").is_file()
            and (root / "cloth_next" / "blender_manifest.toml").is_file()):
        return root
    return None


class SolverResolver:
    def __init__(self, version_probe: Callable[[Path], tuple[str, str, str]]) -> None:
        self._version_probe = version_probe

    def _local(self, mode: SolverMode, layout: BundledSolverLayout, writable: bool) -> ResolvedSolver | None:
        if not layout.executable_path.is_file():
            return None
        package, protocol, schema = self._version_probe(layout.executable_path)
        metadata = layout.source_metadata() if layout.source_metadata_path.is_file() else None
        return ResolvedSolver(mode, layout.root_directory, layout.executable_path, package,
            protocol, schema, ConnectionOwnership.OWNED_PROCESS, metadata, writable)

    def resolve(self, context: SolverResolutionContext) -> ResolvedSolver | None:
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
        found = self._local(SolverMode.EXTENSION_BUNDLED,
            BundledSolverLayout.from_root(context.extension_root / PLATFORM_DIRECTORY), False)
        if found:
            return found
        if context.repository_root is not None:
            found = self._local(SolverMode.REPOSITORY_BUNDLED,
                BundledSolverLayout.from_root(context.repository_root / PLATFORM_DIRECTORY), True)
            if found:
                return found
        if context.external_server_available:
            return ResolvedSolver(SolverMode.EXTERNAL_SERVER, None, None, None, None, None,
                ConnectionOwnership.EXTERNAL_SERVER, None, False)
        return None

