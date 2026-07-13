# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Managed installation pipeline for the external PPF Contact Solver.

Pipeline: user confirmation → official download → temporary file → SHA-256
verification → safe archive inspection → staging extraction → executable
discovery → version probe → protocol check → schema check → real health check
→ atomic publication → activate version.

Any failure preserves the previously active installation: ``current.json`` is
only rewritten after the new version passed its health check. Versions are
installed side by side; nothing is ever installed in place over an active
version. No ``bpy`` access happens here.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Callable

from ..core.errors import ErrorCategory, ErrorRecord
from ..ppf.bootstrap import (atomic_replace_directory, find_single_executable,
                             normalize_bundle_root)
from ..ppf.layout import EXECUTABLE_NAME

from . import download as download_module
from .archive import extract_to_staging
from .install_paths import (ActiveInstallation, ManagedSolverPaths, clear_current,
                            make_current_record, read_current,
                            validate_installation_id, write_current)
from .solver_manifest import SolverCompatibilityEntry
from .states import InstallerState, can_transition
from .update_check import solver_update_available

VersionProbe = Callable[[Path], tuple[str, str, str]]
HealthCheck = Callable[[Path], bool]
Fetcher = Callable[..., Path]


class ManagedSolverInstaller:
    """Synchronous installer core; callers run it off the Blender main thread."""

    def __init__(self, paths: ManagedSolverPaths, entry: SolverCompatibilityEntry, *,
                 probe_version: VersionProbe,
                 health_check: HealthCheck,
                 fetch: Fetcher = download_module.download_asset,
                 forbidden_roots: tuple[Path, ...] = (),
                 is_solver_running: Callable[[], bool] = lambda: False) -> None:
        paths.validate_outside(forbidden_roots)
        self._paths = paths
        self._entry = entry
        self._probe_version = probe_version
        self._health_check = health_check
        self._fetch = fetch
        self._is_solver_running = is_solver_running
        self._cancel = threading.Event()
        self._repair_mode = False
        self._error: ErrorRecord | None = None
        self._download_done = 0
        self._download_total = 0
        self._decline_state = InstallerState.DOWNLOAD_AVAILABLE
        try:
            self._state = self._resolve_local_state(read_current(paths))
        except ValueError:
            # Tampered or corrupted current.json: never trust it, offer repair.
            self._state = InstallerState.REPAIR_REQUIRED

    def _resolve_local_state(self,
                             active: ActiveInstallation | None) -> InstallerState:
        """Purely local session-initialization state: compares the installed
        release identity with the bundled manifest. No download, no network
        request, no solver process, no thread, no installation change."""
        if active is None:
            return InstallerState.NOT_INSTALLED
        if solver_update_available(active, self._entry):
            return InstallerState.UPDATE_AVAILABLE
        return InstallerState.READY

    @property
    def state(self) -> InstallerState:
        return self._state

    @property
    def error(self) -> ErrorRecord | None:
        return self._error

    @property
    def paths(self) -> ManagedSolverPaths:
        return self._paths

    @property
    def download_progress(self) -> tuple[int, int]:
        """(bytes downloaded, expected total bytes); read from the UI thread."""
        return (self._download_done, self._download_total)

    def _note_download_progress(self, done: int, total: int) -> None:
        self._download_done = done
        self._download_total = total

    def active_installation(self) -> ActiveInstallation | None:
        return read_current(self._paths)

    def _set_state(self, target: InstallerState) -> None:
        if not can_transition(self._state, target):
            raise ValueError(f"invalid installer transition "
                             f"{self._state.name} → {target.name}")
        self._state = target

    def _fail(self, message: str, technical: str, *,
              category: ErrorCategory = ErrorCategory.SOLVER_INSTALLATION,
              action: str = "Review the details and retry the installation.") -> ErrorRecord:
        self._error = ErrorRecord.create(category=category, user_message=message,
                                         technical_message=technical,
                                         recommended_action=action, recoverable=True)
        self._state = InstallerState.ERROR
        return self._error

    def request_download(self) -> InstallerState:
        """Move to the explicit confirmation step; never starts any download."""
        self._decline_state = (self._state
                               if self._state is not InstallerState.NOT_INSTALLED
                               else InstallerState.DOWNLOAD_AVAILABLE)
        self._set_state(InstallerState.AWAITING_CONFIRMATION)
        return self._state

    def cancel(self) -> None:
        self._cancel.set()

    def install(self, *, confirmed: bool) -> InstallerState:
        """Run the full pipeline. Requires prior :meth:`request_download`.

        ``confirmed=False`` performs no network or file operation at all.
        """
        if self._state is not InstallerState.AWAITING_CONFIRMATION:
            raise ValueError("install() requires the AWAITING_CONFIRMATION state")
        if not confirmed:
            # A cancelled confirmation returns to the pre-dialog state, so a
            # pending update keeps being shown as available.
            self._repair_mode = False
            self._set_state(self._decline_state)
            return self._state
        previous = read_current(self._paths)
        self._cancel.clear()
        self._error = None
        entry = self._entry
        self._paths.ensure_layout()
        archive_path = self._paths.downloads_dir / entry.official_asset_name
        staging: Path | None = None
        try:
            self._note_download_progress(0, entry.download_size)
            self._set_state(InstallerState.DOWNLOADING)
            self._fetch(entry, archive_path, cancel=self._cancel,
                        progress=self._note_download_progress)
            self._set_state(InstallerState.VERIFYING)
            download_module.verify_sha256(archive_path, entry.sha256)
            self._set_state(InstallerState.EXTRACTING)
            staging = extract_to_staging(archive_path, self._paths.staging_dir)
            self._set_state(InstallerState.INSTALLING)
            executable = find_single_executable(staging)
            if executable.name != EXECUTABLE_NAME:
                raise ValueError(f"unexpected executable {executable.name!r} is not started")
            package, protocol, schema = self._probe_version(executable)
            if protocol != entry.protocol_version:
                raise _CompatibilityFailure(
                    f"protocol {protocol!r} does not match required "
                    f"{entry.protocol_version!r}")
            if schema != entry.schema_version:
                raise _CompatibilityFailure(
                    f"schema {schema!r} does not match required {entry.schema_version!r}")
            if package != entry.solver_package_version:
                raise _CompatibilityFailure(
                    f"package {package!r} does not match the manifest version "
                    f"{entry.solver_package_version!r}")
            self._set_state(InstallerState.HEALTH_CHECKING)
            if entry.health_check_required and not self._health_check(executable):
                raise _HealthCheckFailure("the real solver health check failed")
            bundle_root = normalize_bundle_root(staging)
            relative = executable.relative_to(bundle_root).as_posix()
            # The installation directory is named after the immutable official
            # release tag, never after the internal package version alone:
            # different official releases may report the same package version
            # and must install side by side.
            installation_id = validate_installation_id(entry.official_release_tag)
            version_dir = self._paths.version_dir(installation_id)
            if version_dir.exists():
                if not self._repair_mode:
                    raise ValueError(
                        f"managed release {installation_id} already exists; "
                        "never install in place — remove or repair it "
                        "explicitly")
                atomic_replace_directory(Path(bundle_root), version_dir)
            else:
                version_dir.parent.mkdir(parents=True, exist_ok=True)
                Path(bundle_root).replace(version_dir)
            self._repair_mode = False
            write_current(self._paths, make_current_record(
                installation_id=installation_id,
                solver_package_version=entry.solver_package_version,
                executable_relative=relative,
                official_release_tag=entry.official_release_tag,
                official_asset_name=entry.official_asset_name,
                asset_sha256=entry.sha256))
            self._set_state(InstallerState.READY)
        except download_module.DownloadCancelled:
            self._state = InstallerState.CANCELLING
            archive_path.unlink(missing_ok=True)
            self._restore_after_failure(previous)
            self._set_state(self._resolve_local_state(previous)
                            if previous else InstallerState.DOWNLOAD_AVAILABLE)
        except _CompatibilityFailure as exc:
            self._fail("The downloaded solver is not compatible with this "
                       "Cloth NeXt version.", str(exc),
                       category=ErrorCategory.PROTOCOL_COMPATIBILITY,
                       action="Keep the current installation; report the manifest mismatch.")
            self._restore_after_failure(previous)
        except _HealthCheckFailure as exc:
            self._fail("The new solver version failed its health check and was "
                       "not activated.", str(exc))
            self._restore_after_failure(previous)
        except Exception as exc:  # noqa: BLE001 — every failure must preserve the old install
            self._fail("The solver installation failed. The previous installation "
                       "was preserved.", f"{type(exc).__name__}: {exc}")
            self._restore_after_failure(previous)
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
        return self._state

    def _restore_after_failure(self, previous: ActiveInstallation | None) -> None:
        """The pipeline never touched current.json before success; assert that.

        Restoring writes back the previous record unchanged — a legacy record
        stays in the legacy format and is never migrated by a failed update.
        """
        try:
            current = read_current(self._paths)
        except ValueError:
            current = None
        if previous is None:
            if current is not None:
                clear_current(self._paths)
        elif current != previous:
            write_current(self._paths, previous)

    def remove(self, installation_id: str) -> None:
        """Remove a managed installation; external installations are never touched."""
        if self._is_solver_running():
            raise ValueError("stop the solver before removing a managed installation")
        version_dir = self._paths.version_dir(installation_id)
        resolved = version_dir.resolve()
        if self._paths.versions_dir.resolve() not in resolved.parents:
            raise ValueError("refusing to remove a directory outside the managed root")
        active = read_current(self._paths)
        if version_dir.exists():
            shutil.rmtree(version_dir)
        if active is not None and active.installation_id == installation_id:
            clear_current(self._paths)
            self._state = InstallerState.NOT_INSTALLED

    def prepare_repair(self) -> InstallerState:
        """Queue a reinstall of the active managed version; files are only
        replaced after confirmation, download, verification, and health check."""
        if self._is_solver_running():
            raise ValueError("stop the solver before repairing a managed installation")
        try:
            if read_current(self._paths) is None:
                raise ValueError("no managed installation to repair")
        except ValueError:
            if self._state is not InstallerState.REPAIR_REQUIRED:
                raise
            # Corrupt or tampered metadata is exactly what repair replaces.
        origin = self._state
        if self._state is not InstallerState.REPAIR_REQUIRED:
            self._set_state(InstallerState.REPAIR_REQUIRED)
        self._decline_state = (origin if origin in (InstallerState.READY,
                                                    InstallerState.UPDATE_AVAILABLE)
                               else InstallerState.REPAIR_REQUIRED)
        self._set_state(InstallerState.AWAITING_CONFIRMATION)
        self._repair_mode = True
        return self._state

    def check_for_update(self) -> InstallerState:
        """Re-run the central identity comparison; only the manifest-pinned
        release is ever offered, unknown versions never. The decision compares
        the immutable official release tag and asset hash — the internal
        package version alone never decides it."""
        try:
            active = read_current(self._paths)
        except ValueError:
            self._state = InstallerState.REPAIR_REQUIRED
            return self._state
        target = self._resolve_local_state(active)
        if target is not self._state:
            self._set_state(target)
        return self._state


class _CompatibilityFailure(Exception):
    pass


class _HealthCheckFailure(Exception):
    pass
