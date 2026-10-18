# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Transactional installation of a verified GAIA package.

Archive checksum and size (when the server supplied them) → safe archive
inspection → staging extraction → GAIA identity, per-file SHA256SUMS and
patched-file checksums → version probe → real health check → atomic move into
``versions/<installation id>`` → registry registration. Any failure removes the
staging tree and leaves every existing installation and the selected solver
untouched. Existing GAIA builds stay installed side by side for rollback.

No ``bpy``; runs on a worker thread.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from . import engines
from .archive import extract_to_staging
from .install_paths import ManagedSolverPaths, validate_installation_id
from .solver_registry import SolverInstallation, SolverRegistry, load_registry, write_registry

SERVER = PurePosixPath("target/cuda/release/ppf-cts-server.exe")
_SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")


class GaiaInstallError(RuntimeError):
    """User-facing installation failure; nothing was changed."""


@dataclass(frozen=True, slots=True)
class GaiaInstallResult:
    installation: SolverInstallation
    build: engines.GaiaBuild
    already_installed: bool = False


def installation_id_for(build: engines.GaiaBuild) -> str:
    return validate_installation_id(f"gaia-{build.version}-{build.short_revision}")


def _package_root(staging: Path) -> Path:
    if (staging / engines.GAIA_BUILD_FILE).is_file():
        return staging
    children = [child for child in staging.iterdir()]
    if len(children) == 1 and children[0].is_dir() and (
            children[0] / engines.GAIA_BUILD_FILE).is_file():
        return children[0]
    raise GaiaInstallError("The package is not a GAIA solver package (gaia-build.json is missing).")


def verify_package_tree(root: Path) -> engines.GaiaBuild:
    """Validate identity, the complete checksum list and the patched files."""
    build = engines.read_gaia_build(root)
    if build is None:
        raise GaiaInstallError("The GAIA build description is invalid.")
    sums = root / "SHA256SUMS.txt"
    if not sums.is_file():
        raise GaiaInstallError("The GAIA package has no checksum list.")
    listed = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        match = _SUM_LINE.match(line)
        if not match:
            raise GaiaInstallError("The GAIA checksum list is malformed.")
        digest, name = match.groups()
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise GaiaInstallError("The GAIA checksum list contains an unsafe path.")
        path = root.joinpath(*relative.parts)
        if not path.is_file() or engines.sha256_file(path) != digest:
            raise GaiaInstallError(f"GAIA package file {name} is missing or corrupted.")
        listed.add(relative.as_posix())
    present = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    unexpected = present - listed - {"SHA256SUMS.txt"}
    if unexpected:
        raise GaiaInstallError("The GAIA package contains unlisted files: "
                               + ", ".join(sorted(unexpected)[:3]))
    problem = engines.verify_gaia_files(root, build)
    if problem:
        raise GaiaInstallError(problem)
    if not root.joinpath(*SERVER.parts).is_file():
        raise GaiaInstallError("The GAIA package has no solver server executable.")
    return build


def install_gaia_package(
        archive: Path, paths: ManagedSolverPaths, *,
        probe_version: Callable[[Path], tuple[str, str, str]],
        health_check: Callable[[Path], bool],
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        expected_build_id: str | None = None,
        activate: bool = False,
        cancelled: Callable[[], bool] = lambda: False) -> GaiaInstallResult:
    archive = Path(archive)
    if expected_size is not None and archive.stat().st_size != expected_size:
        raise GaiaInstallError("The GAIA download has the wrong size; it was not installed.")
    if expected_sha256 is not None and engines.sha256_file(archive) != expected_sha256.lower():
        raise GaiaInstallError("The GAIA download failed its SHA-256 check; it was not installed.")
    paths.ensure_layout()
    try:
        staging = extract_to_staging(archive, paths.staging_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise GaiaInstallError(f"The GAIA package could not be unpacked safely: {exc}") from exc
    try:
        root = _package_root(staging)
        build = verify_package_tree(root)
        if expected_build_id is not None and build.build_id != expected_build_id:
            raise GaiaInstallError("The GAIA package is not the build that was requested.")
        if cancelled():
            raise GaiaInstallError("GAIA installation was cancelled.")
        server = root.joinpath(*SERVER.parts)
        package, protocol, schema = probe_version(server)
        if protocol != build.protocol_version:
            raise GaiaInstallError(
                f"The GAIA server reports protocol {protocol}, expected {build.protocol_version}.")
        installation_id = installation_id_for(build)
        registry = load_registry(paths.registry_json)
        target = paths.version_dir(installation_id)
        existing = registry.get(installation_id)
        if existing is not None and target.is_dir():
            if engines.verify_gaia_files(target, build) is None:
                if activate:
                    write_registry(paths.registry_json, registry.select(installation_id))
                return GaiaInstallResult(existing, build, already_installed=True)
        healthy = bool(health_check(server))
        if not healthy:
            raise GaiaInstallError(
                "The GAIA solver did not pass its health check on this computer. "
                "Check the NVIDIA driver (CUDA 12.8) and the Visual C++ runtime.")
        if cancelled():
            raise GaiaInstallError("GAIA installation was cancelled.")
        previous = None
        if target.exists():
            # Repair: keep the damaged copy aside until the new one is in place.
            previous = target.with_name(f".{target.name}.replaced-{uuid.uuid4().hex}")
            os.replace(target, previous)
        try:
            os.replace(root, target)
        except OSError:
            if previous is not None:
                os.replace(previous, target)
            raise
        installation = SolverInstallation(
            installation_id=installation_id,
            display_name=f"GAIA {build.version}",
            source=engines.GAIA_SOURCE,
            root_path=str(target.resolve()),
            executable_path=str(target.joinpath(*SERVER.parts).resolve()),
            frontend_path=str((target / "frontend").resolve()),
            package_version=package, protocol_version=protocol, schema_version=schema,
            official_release_tag=None, managed=True, verified=True, healthy=True,
            channel="gaia", error=None)
        try:
            registry = (registry.update(installation) if existing is not None
                        else registry.register(installation))
            if activate:
                registry = registry.select(installation_id)
            write_registry(paths.registry_json, registry)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            if previous is not None:
                os.replace(previous, target)
            raise
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)
        return GaiaInstallResult(installation, build)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove_gaia_installation(paths: ManagedSolverPaths, registry: SolverRegistry,
                             installation_id: str) -> SolverRegistry:
    """Unregister and delete one GAIA build; never touches AETHER installations."""
    installation = registry.get(installation_id)
    if installation is None or engines.engine_of(installation) != engines.GAIA:
        raise GaiaInstallError("Only an installed GAIA build can be removed here.")
    target = paths.version_dir(installation_id)
    registry = write_registry(paths.registry_json, registry.unregister(installation_id))
    shutil.rmtree(target, ignore_errors=True)
    return registry
