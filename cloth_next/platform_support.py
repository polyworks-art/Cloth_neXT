# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, dependency-free host platform description used by packaging/runtime."""

from __future__ import annotations

import os
import platform as _platform
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    os_name: str
    architecture: str
    blender_platform: str
    solver_platform: str
    companion_filename: str
    companion_build_name: str
    solver_filename: str
    solver_worker_filename: str
    executable_permission_required: bool
    process_tree_strategy: str


WINDOWS_X64 = PlatformSpec(
    "windows", "x86_64", "windows-x64", "windows-x86_64",
    "cloth-next-bake.exe", "Cloth NeXt Bake.exe", "ppf-cts-server.exe",
    "ppf-contact-solver.exe", False, "windows-job-object")
LINUX_X64 = PlatformSpec(
    "linux", "x86_64", "linux-x64", "linux-x86_64",
    "cloth-next-bake", "Cloth NeXt Bake", "ppf-cts-server",
    "ppf-contact-solver", True, "posix-session")

SUPPORTED_PLATFORMS = (WINDOWS_X64, LINUX_X64)


def platform_spec(*, sys_platform: str | None = None,
                  machine: str | None = None) -> PlatformSpec:
    if sys_platform is None and machine is None:
        override = os.environ.get("CLOTH_NEXT_PLATFORM_OVERRIDE")
        if override:
            return spec_for_blender_platform(override)
    system = sys.platform if sys_platform is None else sys_platform
    architecture = (_platform.machine() if machine is None else machine).lower()
    if architecture in {"amd64", "x64"}:
        architecture = "x86_64"
    if system == "win32" and architecture == "x86_64":
        return WINDOWS_X64
    if system.startswith("linux") and architecture == "x86_64":
        return LINUX_X64
    raise RuntimeError(
        f"unsupported Cloth NeXt platform: {system}/{architecture}; "
        "supported platforms are Windows x86_64 and Linux x86_64")


def spec_for_blender_platform(value: str) -> PlatformSpec:
    try:
        return next(item for item in SUPPORTED_PLATFORMS
                    if item.blender_platform == value)
    except StopIteration as exc:
        raise ValueError(f"unsupported Blender platform {value!r}") from exc


def spec_for_solver_platform(value: str) -> PlatformSpec:
    try:
        return next(item for item in SUPPORTED_PLATFORMS
                    if item.solver_platform == value)
    except StopIteration as exc:
        raise ValueError(f"unsupported solver platform {value!r}") from exc


def managed_solver_root(spec: PlatformSpec | None = None,
                        *, environ: dict[str, str] | None = None,
                        home: Path | None = None) -> Path:
    current = spec or platform_spec()
    env = os.environ if environ is None else environ
    if current.os_name == "windows":
        base = env.get("LOCALAPPDATA") or str((home or Path.home()) / ".local" / "share")
    else:
        base = env.get("XDG_DATA_HOME") or str((home or Path.home()) / ".local" / "share")
    return (Path(base) / "ClothNeXt" / "solver").resolve()
