"""Read-only validation of user-selected external solver installations.

Cloth NeXt may validate, probe versions, health-check, and start/stop only the
processes it started itself. It never modifies, deletes, or updates external
files, and it never terminates an external server it did not start.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cloth_next.ppf.layout import EXECUTABLE_NAME, BundledSolverLayout

from .modes import InstallationMode, permissions_for
from .solver_manifest import SolverCompatibilityEntry


@dataclass(frozen=True, slots=True)
class ExternalInstallation:
    root: Path
    executable: Path
    package_version: str
    protocol_version: str
    schema_version: str
    protocol_compatible: bool
    schema_compatible: bool

    @property
    def compatible(self) -> bool:
        return self.protocol_compatible and self.schema_compatible

    @property
    def mode(self) -> InstallationMode:
        return InstallationMode.EXTERNAL_INSTALLATION


def validate_external_installation(selection: Path,
                                   probe_version: Callable[[Path], tuple[str, str, str]],
                                   entry: SolverCompatibilityEntry,
                                   ) -> ExternalInstallation:
    """Validate a user-selected folder or executable without modifying it."""
    path = selection.expanduser().resolve()
    root = path.parent if path.is_file() else path
    layout = BundledSolverLayout.from_root(root)
    if not layout.executable_path.is_file():
        raise ValueError(f"no {EXECUTABLE_NAME} found under {root}")
    if layout.executable_path.name != EXECUTABLE_NAME:
        raise ValueError(f"unknown executable {layout.executable_path.name!r} "
                         "is not probed or started")
    package, protocol, schema = probe_version(layout.executable_path)
    assert not permissions_for(InstallationMode.EXTERNAL_INSTALLATION).may_modify_files
    return ExternalInstallation(
        root=layout.root_directory,
        executable=layout.executable_path,
        package_version=package,
        protocol_version=protocol,
        schema_version=schema,
        protocol_compatible=protocol == entry.protocol_version,
        schema_compatible=schema == entry.schema_version,
    )
