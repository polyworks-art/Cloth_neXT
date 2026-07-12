"""Solver installer states, their UI descriptors, and valid transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Mapping

from cloth_next.core.errors import ErrorCategory


class InstallerState(Enum):
    NOT_INSTALLED = auto()
    CHECKING_COMPATIBILITY = auto()
    DOWNLOAD_AVAILABLE = auto()
    AWAITING_CONFIRMATION = auto()
    DOWNLOADING = auto()
    VERIFYING = auto()
    EXTRACTING = auto()
    INSTALLING = auto()
    HEALTH_CHECKING = auto()
    READY = auto()
    UPDATE_AVAILABLE = auto()
    REPAIR_REQUIRED = auto()
    INCOMPATIBLE = auto()
    CANCELLING = auto()
    ERROR = auto()


class InstallerAction(Enum):
    DOWNLOAD_OFFICIAL_SOLVER = auto()
    SELECT_EXISTING_INSTALLATION = auto()
    OPEN_OFFICIAL_DOWNLOAD_PAGE = auto()
    CONFIRM_DOWNLOAD = auto()
    CANCEL = auto()
    RUN_HEALTH_CHECK = auto()
    CHECK_FOR_COMPATIBLE_UPDATE = auto()
    INSTALL_COMPATIBLE_VERSION = auto()
    REPAIR_MANAGED_INSTALLATION = auto()
    REMOVE_MANAGED_INSTALLATION = auto()
    OPEN_INSTALLATION_FOLDER = auto()
    SELECT_ANOTHER_INSTALLATION = auto()
    VIEW_DETAILS = auto()
    RETRY = auto()


@dataclass(frozen=True, slots=True)
class StateDescriptor:
    ui_message: str
    allowed_actions: tuple[InstallerAction, ...]
    error_category: ErrorCategory | None
    recommended_action: str


_A = InstallerAction

DESCRIPTORS: Mapping[InstallerState, StateDescriptor] = MappingProxyType({
    InstallerState.NOT_INSTALLED: StateDescriptor(
        "No PPF Contact Solver is installed. The solver is external software by "
        "ST Tech / ZOZO and is downloaded separately after your confirmation.",
        (_A.DOWNLOAD_OFFICIAL_SOLVER, _A.SELECT_EXISTING_INSTALLATION,
         _A.OPEN_OFFICIAL_DOWNLOAD_PAGE),
        None, "Download the official solver or select an existing installation."),
    InstallerState.CHECKING_COMPATIBILITY: StateDescriptor(
        "Checking the selected solver installation for compatibility…",
        (_A.CANCEL,), None, "Wait for the compatibility check to finish."),
    InstallerState.DOWNLOAD_AVAILABLE: StateDescriptor(
        "A verified official solver release is available for separate download.",
        (_A.DOWNLOAD_OFFICIAL_SOLVER, _A.SELECT_EXISTING_INSTALLATION,
         _A.OPEN_OFFICIAL_DOWNLOAD_PAGE, _A.VIEW_DETAILS),
        None, "Start the download to review and confirm it."),
    InstallerState.AWAITING_CONFIRMATION: StateDescriptor(
        "Please review the external-software notice and confirm the download.",
        (_A.CONFIRM_DOWNLOAD, _A.CANCEL, _A.OPEN_OFFICIAL_DOWNLOAD_PAGE,
         _A.VIEW_DETAILS),
        None, "Confirm to download from the official source, or cancel."),
    InstallerState.DOWNLOADING: StateDescriptor(
        "Downloading the official solver release from st-tech/ppf-contact-solver…",
        (_A.CANCEL,), None, "Wait for the download or cancel it."),
    InstallerState.VERIFYING: StateDescriptor(
        "Verifying the SHA-256 checksum of the downloaded archive…",
        (), None, "Wait for verification to finish."),
    InstallerState.EXTRACTING: StateDescriptor(
        "Safely extracting the verified archive into the staging area…",
        (), None, "Wait for extraction to finish."),
    InstallerState.INSTALLING: StateDescriptor(
        "Probing solver version, protocol, and schema…",
        (), None, "Wait for installation to finish."),
    InstallerState.HEALTH_CHECKING: StateDescriptor(
        "Running the real solver health check before activation…",
        (), None, "Wait for the health check to finish."),
    InstallerState.READY: StateDescriptor(
        "The PPF Contact Solver is installed and healthy.",
        (_A.RUN_HEALTH_CHECK, _A.CHECK_FOR_COMPATIBLE_UPDATE,
         _A.REPAIR_MANAGED_INSTALLATION, _A.REMOVE_MANAGED_INSTALLATION,
         _A.OPEN_INSTALLATION_FOLDER),
        None, "No action required."),
    InstallerState.UPDATE_AVAILABLE: StateDescriptor(
        "A newer manifest-verified compatible solver version is available.",
        (_A.INSTALL_COMPATIBLE_VERSION, _A.RUN_HEALTH_CHECK,
         _A.OPEN_INSTALLATION_FOLDER, _A.VIEW_DETAILS),
        None, "Install the compatible update when no simulation is running."),
    InstallerState.REPAIR_REQUIRED: StateDescriptor(
        "The managed solver installation is damaged or incomplete.",
        (_A.REPAIR_MANAGED_INSTALLATION, _A.REMOVE_MANAGED_INSTALLATION,
         _A.SELECT_EXISTING_INSTALLATION, _A.VIEW_DETAILS),
        ErrorCategory.SOLVER_INSTALLATION, "Repair the managed installation."),
    InstallerState.INCOMPATIBLE: StateDescriptor(
        "The installed solver is not compatible with this Cloth NeXt version.",
        (_A.INSTALL_COMPATIBLE_VERSION, _A.SELECT_ANOTHER_INSTALLATION,
         _A.VIEW_DETAILS),
        ErrorCategory.PROTOCOL_COMPATIBILITY,
        "Install the compatible version listed in the compatibility manifest."),
    InstallerState.CANCELLING: StateDescriptor(
        "Cancelling the current download or installation…",
        (), None, "Wait for the cancellation to complete."),
    InstallerState.ERROR: StateDescriptor(
        "The last solver installation step failed. The previously active "
        "installation was preserved.",
        (_A.RETRY, _A.SELECT_EXISTING_INSTALLATION,
         _A.OPEN_OFFICIAL_DOWNLOAD_PAGE, _A.VIEW_DETAILS),
        ErrorCategory.SOLVER_INSTALLATION, "Review the details and retry."),
})

_S = InstallerState

VALID_TRANSITIONS: Mapping[InstallerState, frozenset[InstallerState]] = MappingProxyType({
    _S.NOT_INSTALLED: frozenset({_S.CHECKING_COMPATIBILITY, _S.DOWNLOAD_AVAILABLE,
                                 _S.AWAITING_CONFIRMATION, _S.ERROR}),
    _S.CHECKING_COMPATIBILITY: frozenset({_S.READY, _S.INCOMPATIBLE, _S.REPAIR_REQUIRED,
                                          _S.NOT_INSTALLED, _S.UPDATE_AVAILABLE,
                                          _S.DOWNLOAD_AVAILABLE, _S.ERROR}),
    _S.DOWNLOAD_AVAILABLE: frozenset({_S.AWAITING_CONFIRMATION,
                                      _S.CHECKING_COMPATIBILITY, _S.ERROR}),
    _S.AWAITING_CONFIRMATION: frozenset({_S.DOWNLOADING, _S.DOWNLOAD_AVAILABLE,
                                         _S.NOT_INSTALLED, _S.ERROR}),
    _S.DOWNLOADING: frozenset({_S.VERIFYING, _S.CANCELLING, _S.ERROR}),
    _S.VERIFYING: frozenset({_S.EXTRACTING, _S.CANCELLING, _S.ERROR}),
    _S.EXTRACTING: frozenset({_S.INSTALLING, _S.CANCELLING, _S.ERROR}),
    _S.INSTALLING: frozenset({_S.HEALTH_CHECKING, _S.CANCELLING, _S.ERROR}),
    _S.HEALTH_CHECKING: frozenset({_S.READY, _S.ERROR}),
    _S.READY: frozenset({_S.CHECKING_COMPATIBILITY, _S.UPDATE_AVAILABLE,
                         _S.REPAIR_REQUIRED, _S.INCOMPATIBLE, _S.NOT_INSTALLED,
                         _S.ERROR}),
    _S.UPDATE_AVAILABLE: frozenset({_S.AWAITING_CONFIRMATION, _S.READY,
                                    _S.CHECKING_COMPATIBILITY, _S.ERROR}),
    _S.REPAIR_REQUIRED: frozenset({_S.AWAITING_CONFIRMATION, _S.NOT_INSTALLED,
                                   _S.CHECKING_COMPATIBILITY, _S.ERROR}),
    _S.INCOMPATIBLE: frozenset({_S.AWAITING_CONFIRMATION, _S.CHECKING_COMPATIBILITY,
                                _S.NOT_INSTALLED, _S.ERROR}),
    _S.CANCELLING: frozenset({_S.DOWNLOAD_AVAILABLE, _S.NOT_INSTALLED, _S.READY,
                              _S.ERROR}),
    _S.ERROR: frozenset({_S.NOT_INSTALLED, _S.DOWNLOAD_AVAILABLE,
                         _S.CHECKING_COMPATIBILITY, _S.AWAITING_CONFIRMATION,
                         _S.READY}),
})


def can_transition(source: InstallerState, target: InstallerState) -> bool:
    return target in VALID_TRANSITIONS[source]


def describe(state: InstallerState) -> StateDescriptor:
    return DESCRIPTORS[state]
