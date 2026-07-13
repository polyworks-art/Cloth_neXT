# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

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
    #: Immutable official release tag of the installed managed release, or a
    #: "Legacy installation …" label when the identity is unknown.
    release_label: str = ""


@dataclass(frozen=True, slots=True)
class UpdateAlert:
    """Content of the red preferences warning box.

    Present only when a managed installation exists and the bundled manifest
    pins a verified download whose immutable release identity differs from
    the installed one. Rendering it never starts any download.
    """
    title: str
    lines: tuple[str, ...]
    action: InstallerAction
    action_text: str


@dataclass(frozen=True, slots=True)
class SolverSection:
    status: SectionStatus
    rows: tuple[tuple[str, str], ...]
    actions: tuple[InstallerAction, ...]
    message: str
    update_alert: UpdateAlert | None = None


_BUSY_STATES = frozenset({
    InstallerState.CHECKING_COMPATIBILITY, InstallerState.DOWNLOADING,
    InstallerState.VERIFYING, InstallerState.EXTRACTING, InstallerState.INSTALLING,
    InstallerState.HEALTH_CHECKING, InstallerState.CANCELLING,
    InstallerState.AWAITING_CONFIRMATION,
})


def format_download_progress(done: int, total: int) -> str:
    """Human-readable download progress, e.g. '123 / 430 MiB (28%)'."""
    done_mib = done / (1024 * 1024)
    if total <= 0:
        return f"{done_mib:.0f} MiB"
    total_mib = total / (1024 * 1024)
    percent = min(100, 100 * done // total)
    return f"{done_mib:.0f} / {total_mib:.0f} MiB ({percent}%)"


def build_section(installer_state: InstallerState,
                  entry: SolverCompatibilityEntry | None,
                  download_disabled_reason: str | None,
                  installed: InstalledInfo | None,
                  download_progress: str | None = None) -> SolverSection:
    descriptor = describe(installer_state)
    actions = descriptor.allowed_actions
    if entry is None:
        # No verified official source: automatic download stays disabled.
        actions = tuple(action for action in actions
                        if action not in (InstallerAction.DOWNLOAD_OFFICIAL_SOLVER,
                                          InstallerAction.CONFIRM_DOWNLOAD,
                                          InstallerAction.INSTALL_COMPATIBLE_VERSION))
    if installer_state in _BUSY_STATES:
        rows: tuple[tuple[str, str], ...] = (("Status", descriptor.ui_message),)
        if installer_state is InstallerState.DOWNLOADING and download_progress:
            rows += (("Progress", download_progress),)
        return SolverSection(SectionStatus.BUSY, rows, actions, descriptor.ui_message)
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
    if installed.release_label:
        rows += (("Installed Release", installed.release_label),)
    alert = None
    if (status is SectionStatus.UPDATE_AVAILABLE and entry is not None
            and installed.mode is InstallationMode.MANAGED_INSTALLATION):
        alert = build_update_alert(installed.release_label or "Unknown release",
                                   entry)
    return SolverSection(status, rows, actions, descriptor.ui_message, alert)


def build_update_alert(installed_release: str,
                       entry: SolverCompatibilityEntry) -> UpdateAlert:
    """The artist-facing red warning shown when a verified update exists."""
    return UpdateAlert(
        title="Solver Update Available",
        lines=(
            "A newer verified PPF Contact Solver is available for Cloth NeXt.",
            f"Installed: {installed_release}",
            f"Available: {entry.official_release_tag}",
            "The new version will be installed alongside the current one.",
            "Your existing solver remains active until the download and "
            "health check succeed.",
        ),
        action=InstallerAction.INSTALL_COMPATIBLE_VERSION,
        action_text="Install Compatible Solver Update")


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
