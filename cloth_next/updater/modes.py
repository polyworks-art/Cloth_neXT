"""Installation modes and what Cloth NeXt is allowed to do in each of them."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from types import MappingProxyType
from typing import Mapping


class InstallationMode(Enum):
    MANAGED_INSTALLATION = auto()
    EXTERNAL_INSTALLATION = auto()
    EXTERNAL_SERVER = auto()


@dataclass(frozen=True, slots=True)
class ModePermissions:
    may_run_health_check: bool
    may_install_versions_side_by_side: bool
    may_switch_active_version: bool
    may_repair: bool
    may_remove: bool
    may_modify_files: bool
    may_start_process: bool
    may_stop_started_process: bool
    may_stop_external_process: bool
    may_update: bool


PERMISSIONS: Mapping[InstallationMode, ModePermissions] = MappingProxyType({
    InstallationMode.MANAGED_INSTALLATION: ModePermissions(
        may_run_health_check=True, may_install_versions_side_by_side=True,
        may_switch_active_version=True, may_repair=True, may_remove=True,
        may_modify_files=True, may_start_process=True,
        may_stop_started_process=True, may_stop_external_process=False,
        may_update=True),
    InstallationMode.EXTERNAL_INSTALLATION: ModePermissions(
        may_run_health_check=True, may_install_versions_side_by_side=False,
        may_switch_active_version=False, may_repair=False, may_remove=False,
        may_modify_files=False, may_start_process=True,
        may_stop_started_process=True, may_stop_external_process=False,
        may_update=False),
    InstallationMode.EXTERNAL_SERVER: ModePermissions(
        may_run_health_check=True, may_install_versions_side_by_side=False,
        may_switch_active_version=False, may_repair=False, may_remove=False,
        may_modify_files=False, may_start_process=False,
        may_stop_started_process=False, may_stop_external_process=False,
        may_update=False),
})


def permissions_for(mode: InstallationMode) -> ModePermissions:
    return PERMISSIONS[mode]
