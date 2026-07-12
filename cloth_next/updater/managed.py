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

from cloth_next.core.errors import ErrorCategory, ErrorRecord
from cloth_next.ppf.bootstrap import (atomic_replace_directory, find_single_executable,
                                      normalize_bundle_root)
from cloth_next.ppf.layout import EXECUTABLE_NAME

from . import download as download_module
from .archive import extract_to_staging
from .install_paths import (ActiveInstallation, ManagedSolverPaths, clear_current,
                            read_current, write_current)
from .solver_manifest import SolverCompatibilityEntry
from .states import InstallerState, can_transition

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
        self._state = (InstallerState.READY if read_current(paths) is not None
                       else InstallerState.NOT_INSTALLED)

    @property
    def state(self) -> InstallerState:
        return self._state

    @property
    def error(self) -> ErrorRecord | None:
        return self._error

    @property
    def paths(self) -> ManagedSolverPaths:
        return self._paths

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
            self._repair_mode = False
            self._set_state(InstallerState.DOWNLOAD_AVAILABLE)
            return self._state
        previous = read_current(self._paths)
        self._cancel.clear()
        self._error = None
        entry = self._entry
        self._paths.ensure_layout()
        archive_path = self._paths.downloads_dir / entry.official_asset_name
        staging: Path | None = None
        try:
            self._set_state(InstallerState.DOWNLOADING)
            self._fetch(entry, archive_path, cancel=self._cancel)
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
            version_dir = self._paths.version_dir(entry.solver_package_version)
            if version_dir.exists():
                if not self._repair_mode:
                    raise ValueError(
                        f"managed version {entry.solver_package_version} already "
                        "exists; never install in place — remove or repair it "
                        "explicitly")
                atomic_replace_directory(Path(bundle_root), version_dir)
            else:
                version_dir.parent.mkdir(parents=True, exist_ok=True)
                Path(bundle_root).replace(version_dir)
            self._repair_mode = False
            write_current(self._paths, entry.solver_package_version, relative)
            self._set_state(InstallerState.READY)
        except download_module.DownloadCancelled:
            self._state = InstallerState.CANCELLING
            archive_path.unlink(missing_ok=True)
            self._restore_after_failure(previous)
            self._set_state(InstallerState.READY if previous
                            else InstallerState.DOWNLOAD_AVAILABLE)
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
        """The pipeline never touched current.json before success; assert that."""
        current = read_current(self._paths)
        if previous is None:
            if current is not None:
                clear_current(self._paths)
        elif current is None or current.version != previous.version:
            write_current(self._paths, previous.version, previous.executable_relative)

    def remove(self, version: str) -> None:
        """Remove a managed version; external installations are never touched."""
        if self._is_solver_running():
            raise ValueError("stop the solver before removing a managed installation")
        version_dir = self._paths.version_dir(version)
        resolved = version_dir.resolve()
        if self._paths.versions_dir.resolve() not in resolved.parents:
            raise ValueError("refusing to remove a directory outside the managed root")
        active = read_current(self._paths)
        if version_dir.exists():
            shutil.rmtree(version_dir)
        if active is not None and active.version == version:
            clear_current(self._paths)
            self._state = InstallerState.NOT_INSTALLED

    def prepare_repair(self) -> InstallerState:
        """Queue a reinstall of the active managed version; files are only
        replaced after confirmation, download, verification, and health check."""
        if self._is_solver_running():
            raise ValueError("stop the solver before repairing a managed installation")
        if read_current(self._paths) is None:
            raise ValueError("no managed installation to repair")
        if self._state is not InstallerState.REPAIR_REQUIRED:
            self._set_state(InstallerState.REPAIR_REQUIRED)
        self._set_state(InstallerState.AWAITING_CONFIRMATION)
        self._repair_mode = True
        return self._state

    def check_for_update(self) -> InstallerState:
        """Offer only the manifest-pinned version; unknown versions are never offered."""
        active = read_current(self._paths)
        if active is None:
            self._state = InstallerState.NOT_INSTALLED
            return self._state
        if active.version != self._entry.solver_package_version:
            self._set_state(InstallerState.UPDATE_AVAILABLE)
        return self._state


class _CompatibilityFailure(Exception):
    pass


class _HealthCheckFailure(Exception):
    pass
