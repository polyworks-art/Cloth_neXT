"""Pure presentation model for the "PPF Contact Solver" preferences section.

No ``bpy`` imports: the Blender preferences panel renders exactly what this
module computes, which keeps the required labeling, gating, and action sets
testable without Blender.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from .install_paths import ManagedSolverPaths
from .modes import InstallationMode
from .solver_manifest import SolverCompatibilityEntry
from .states import InstallerAction, InstallerState, describe

EXTERNAL_SOFTWARE_NOTICE = "Source: External software by ST Tech / ZOZO"


class SectionStatus(Enum):
    NOT_INSTALLED = auto()
    BUSY = auto()
    READY = auto()
    UPDATE_AVAILABLE = auto()
    INCOMPATIBLE = auto()
    ERROR = auto()


@dataclass(frozen=True, slots=True)
class InstalledInfo:
    mode: InstallationMode
    package_version: str
    protocol_version: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class SolverSection:
    status: SectionStatus
    rows: tuple[tuple[str, str], ...]
    actions: tuple[InstallerAction, ...]
    message: str


_BUSY_STATES = frozenset({
    InstallerState.CHECKING_COMPATIBILITY, InstallerState.DOWNLOADING,
    InstallerState.VERIFYING, InstallerState.EXTRACTING, InstallerState.INSTALLING,
    InstallerState.HEALTH_CHECKING, InstallerState.CANCELLING,
    InstallerState.AWAITING_CONFIRMATION,
})


def build_section(installer_state: InstallerState,
                  entry: SolverCompatibilityEntry | None,
                  download_disabled_reason: str | None,
                  installed: InstalledInfo | None) -> SolverSection:
    descriptor = describe(installer_state)
    actions = descriptor.allowed_actions
    if entry is None:
        # No verified official source: automatic download stays disabled.
        actions = tuple(action for action in actions
                        if action not in (InstallerAction.DOWNLOAD_OFFICIAL_SOLVER,
                                          InstallerAction.CONFIRM_DOWNLOAD,
                                          InstallerAction.INSTALL_COMPATIBLE_VERSION))
    if installer_state in _BUSY_STATES:
        return SolverSection(SectionStatus.BUSY, (("Status", descriptor.ui_message),),
                             actions, descriptor.ui_message)
    if installer_state is InstallerState.ERROR:
        return SolverSection(SectionStatus.ERROR, (("Status", "Error"),),
                             actions, descriptor.ui_message)
    if installer_state is InstallerState.INCOMPATIBLE and installed is not None:
        rows = (("Status", "Incompatible"),
                ("Installed Protocol", installed.protocol_version),
                ("Required Protocol", entry.protocol_version if entry else "unknown"))
        return SolverSection(SectionStatus.INCOMPATIBLE, rows, actions,
                             descriptor.ui_message)
    if installed is None:
        rows = (("Status", "Not Installed"),
                ("Required Protocol", entry.protocol_version if entry else "unknown"),
                ("Source", "External software by ST Tech / ZOZO"))
        message = descriptor.ui_message
        if download_disabled_reason:
            message = (f"{message} Automatic download is disabled: "
                       f"{download_disabled_reason}")
        return SolverSection(SectionStatus.NOT_INSTALLED, rows, actions, message)
    status = (SectionStatus.UPDATE_AVAILABLE
              if installer_state is InstallerState.UPDATE_AVAILABLE
              else SectionStatus.READY)
    rows = (("Status", "Ready" if status is SectionStatus.READY else "Update Available"),
            ("Installed Version", installed.package_version),
            ("Compatible Version",
             entry.solver_package_version if entry else "unknown"),
            ("Protocol", installed.protocol_version),
            ("Schema", installed.schema_version),
            ("Installation",
             "Managed" if installed.mode is InstallationMode.MANAGED_INSTALLATION
             else "External"))
    return SolverSection(status, rows, actions, descriptor.ui_message)


def confirmation_lines(entry: SolverCompatibilityEntry,
                       paths: ManagedSolverPaths) -> tuple[str, ...]:
    """The confirmation the user must see before any download starts."""
    size_mib = entry.download_size / (1024 * 1024)
    return (
        "Cloth NeXt requires the external PPF Contact Solver.",
        "",
        "The solver is developed and distributed by ST Tech / ZOZO.",
        "It is not included in or owned by Cloth NeXt.",
        "Cloth NeXt only provides the Blender integration.",
        "",
        f"Source: Official {entry.official_repository} release "
        f"{entry.official_release_tag}",
        f"Version: {entry.solver_package_version} "
        f"(protocol {entry.protocol_version}, schema {entry.schema_version})",
        f"Download size: {entry.download_size:,} bytes (~{size_mib:.0f} MiB)",
        f"Installation location: {paths.versions_dir}",
    )
