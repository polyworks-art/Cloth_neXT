# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, fail-closed deletion for authenticated Cloth NeXt artifacts.

Callers remain responsible for authenticating ownership.  This module adds a
second, central safety boundary: the target must remain inside the supplied
owned root, retries are limited to transient Windows lock errors, and any
tombstone stays inside that same root.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import errno
import os
from pathlib import Path
import shutil
import time
import uuid

from .logging import get_logger, log_with_context


TOMBSTONE_PREFIX = ".clothnext-delete-"
DEFAULT_BACKOFF_SECONDS = (0.01, 0.03, 0.08)
_TRANSIENT_WINDOWS_ERRORS = frozenset({5, 32, 33})


class UnsafeDeleteError(ValueError):
    """The caller did not prove a narrow Cloth NeXt-owned deletion scope."""


class DeleteFailedError(OSError):
    """Artist-safe cleanup failure with detailed local-log diagnostics."""

    def __init__(self, result: "DeleteResult") -> None:
        super().__init__(
            "Temporary or partial files could not be removed. "
            "Close applications using the cache and try again.")
        self.result = result
        self.technical_diagnostic = result.technical_diagnostic()


class DeleteFailureKind(str, Enum):
    NONE = "none"
    MISSING = "missing"
    SHARING_VIOLATION = "sharing_violation"
    PERMISSION = "permission"
    OTHER_OS_ERROR = "other_os_error"


@dataclass(frozen=True, slots=True)
class DeleteResult:
    path: Path
    root: Path
    lifecycle_stage: str
    artifact_type: str
    removed: bool = False
    missing: bool = False
    tombstoned: bool = False
    tombstone_path: Path | None = None
    attempts: int = 0
    retries: int = 0
    failure_kind: DeleteFailureKind = DeleteFailureKind.NONE
    error_type: str = ""
    error_message: str = ""

    @property
    def success(self) -> bool:
        # A tombstone makes the canonical cache name immediately reusable.
        # Its later removal is bounded and idempotent.
        return self.removed or self.missing or self.tombstoned

    def technical_diagnostic(self) -> str:
        return (
            f"stage={self.lifecycle_stage}; artifact={self.artifact_type}; "
            f"path={self.path}; failure={self.failure_kind.value}; "
            f"attempts={self.attempts}; retries={self.retries}; "
            f"tombstoned={self.tombstoned}; "
            f"tombstone_pending={bool(self.tombstone_path)}; "
            f"error_type={self.error_type or 'none'}; "
            f"error={self.error_message or 'none'}")


def _resolved_contained(path: Path, root: Path, *, allow_root: bool) \
        -> tuple[Path, Path]:
    resolved_root = Path(root).expanduser().resolve()
    resolved_path = Path(path).expanduser().resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise UnsafeDeleteError(
            "refusing Cloth NeXt cleanup outside the authenticated root") from exc
    if resolved_path == resolved_root and not allow_root:
        raise UnsafeDeleteError(
            "refusing to delete the authenticated root without explicit scope")
    return resolved_path, resolved_root


def _delete_once(path: Path, *, recursive: bool) -> None:
    if recursive and path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _replace_once(source: Path, target: Path) -> None:
    os.replace(source, target)


def _failure_kind(exc: OSError, *, windows: bool) -> DeleteFailureKind:
    winerror = getattr(exc, "winerror", None)
    if windows and (winerror in {32, 33}
                    or "sharing violation" in str(exc).lower()
                    or "used by another process" in str(exc).lower()):
        return DeleteFailureKind.SHARING_VIOLATION
    if isinstance(exc, PermissionError) or winerror == 5 \
            or getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM}:
        return DeleteFailureKind.PERMISSION
    return DeleteFailureKind.OTHER_OS_ERROR


def _transient(exc: OSError, *, windows: bool) -> bool:
    if not windows:
        return False
    winerror = getattr(exc, "winerror", None)
    return (isinstance(exc, PermissionError)
            or winerror in _TRANSIENT_WINDOWS_ERRORS
            or getattr(exc, "errno", None) in {errno.EACCES, errno.EPERM}
            or "sharing violation" in str(exc).lower()
            or "used by another process" in str(exc).lower())


def _result(*, path: Path, root: Path, lifecycle_stage: str,
            artifact_type: str, attempts: int, exc: OSError | None = None,
            removed: bool = False, missing: bool = False,
            tombstoned: bool = False,
            tombstone_path: Path | None = None,
            windows: bool = os.name == "nt") -> DeleteResult:
    kind = (DeleteFailureKind.MISSING if missing else
            _failure_kind(exc, windows=windows) if exc is not None else
            DeleteFailureKind.NONE)
    return DeleteResult(
        path=path, root=root, lifecycle_stage=lifecycle_stage,
        artifact_type=artifact_type, removed=removed, missing=missing,
        tombstoned=tombstoned, tombstone_path=tombstone_path,
        attempts=attempts, retries=max(0, attempts - 1), failure_kind=kind,
        error_type=type(exc).__name__ if exc is not None else "",
        error_message=str(exc) if exc is not None else "")


def delete_owned(path: Path, *, root: Path, ownership_authenticated: bool,
                 lifecycle_stage: str, artifact_type: str,
                 recursive: bool = False, allow_root: bool = False,
                 tombstone: bool = True,
                 backoff_seconds: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
                 windows: bool | None = None) -> DeleteResult:
    """Delete one authenticated artifact with bounded Windows lock recovery.

    ``ownership_authenticated`` is deliberately explicit.  A name match alone
    never proves ownership; legacy/unowned caches must pass ``False`` and are
    rejected before any filesystem mutation.
    """
    if not ownership_authenticated:
        raise UnsafeDeleteError(
            "refusing to delete an unauthenticated Cloth NeXt artifact")
    target, owned_root = _resolved_contained(
        Path(path), Path(root), allow_root=allow_root)
    is_windows = os.name == "nt" if windows is None else bool(windows)
    attempts = 0
    last_error: OSError | None = None
    delays = (0.0, *tuple(max(0.0, float(value))
                           for value in backoff_seconds))
    for delay in delays:
        if delay:
            time.sleep(delay)
        attempts += 1
        try:
            _delete_once(target, recursive=recursive)
            return _result(
                path=target, root=owned_root,
                lifecycle_stage=lifecycle_stage,
                artifact_type=artifact_type, attempts=attempts,
                removed=True, windows=is_windows)
        except FileNotFoundError:
            return _result(
                path=target, root=owned_root,
                lifecycle_stage=lifecycle_stage,
                artifact_type=artifact_type, attempts=attempts,
                missing=True, windows=is_windows)
        except OSError as exc:
            last_error = exc
            if not _transient(exc, windows=is_windows):
                break

    if (tombstone and last_error is not None
            and _transient(last_error, windows=is_windows)):
        grave = target.with_name(
            f"{TOMBSTONE_PREFIX}{uuid.uuid4().hex}-{target.name}")
        # The target and tombstone share a parent, hence a volume and the same
        # already-authenticated containment boundary.
        try:
            _replace_once(target, grave)
        except FileNotFoundError:
            return _result(
                path=target, root=owned_root,
                lifecycle_stage=lifecycle_stage,
                artifact_type=artifact_type, attempts=attempts,
                missing=True, windows=is_windows)
        except OSError as rename_error:
            last_error = rename_error
        else:
            try:
                _delete_once(grave, recursive=recursive)
                return _result(
                    path=target, root=owned_root,
                    lifecycle_stage=lifecycle_stage,
                    artifact_type=artifact_type, attempts=attempts,
                    removed=True, tombstoned=True, windows=is_windows)
            except FileNotFoundError:
                return _result(
                    path=target, root=owned_root,
                    lifecycle_stage=lifecycle_stage,
                    artifact_type=artifact_type, attempts=attempts,
                    removed=True, tombstoned=True, windows=is_windows)
            except OSError as tombstone_error:
                outcome = _result(
                    path=target, root=owned_root,
                    lifecycle_stage=lifecycle_stage,
                    artifact_type=artifact_type, attempts=attempts,
                    tombstoned=True, tombstone_path=grave,
                    exc=tombstone_error, windows=is_windows)
                log_with_context(
                    get_logger("cleanup"), 30,
                    "Cloth NeXt tombstone remains pending", {
                        "diagnostic": outcome.technical_diagnostic()})
                return outcome

    assert last_error is not None
    outcome = _result(
        path=target, root=owned_root, lifecycle_stage=lifecycle_stage,
        artifact_type=artifact_type, attempts=attempts, exc=last_error,
        windows=is_windows)
    log_with_context(
        get_logger("cleanup"), 40, "Cloth NeXt cleanup failed", {
            "diagnostic": outcome.technical_diagnostic()})
    return outcome


def cleanup_tombstones(root: Path, *, ownership_authenticated: bool,
                       lifecycle_stage: str, max_entries: int = 128,
                       recursive: bool = True) -> tuple[DeleteResult, ...]:
    """Retry a bounded set of tombstones inside an authenticated owned root."""
    if not ownership_authenticated:
        raise UnsafeDeleteError(
            "refusing to scan tombstones in an unauthenticated root")
    owned_root = Path(root).expanduser().resolve()
    limit = max(0, int(max_entries))
    if not limit:
        return ()
    candidates = []
    pending = [(owned_root, 0)]
    remaining = max(128, limit * 8)
    # Bound directory entries examined, not just deletions after a full scan.
    while pending and remaining and len(candidates) < limit:
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    remaining -= 1
                    if not entry.is_symlink():
                        candidate = Path(entry.path)
                        # Junctions must not redirect cleanup into another tree.
                        if candidate.resolve() == candidate.absolute():
                            if entry.name.startswith(TOMBSTONE_PREFIX):
                                candidates.append(candidate)
                            elif recursive and depth < 32 and entry.is_dir(follow_symlinks=False):
                                pending.append((candidate, depth + 1))
                    if not remaining or len(candidates) >= limit:
                        break
        except OSError:
            continue
    results = []
    for candidate in candidates:
        results.append(delete_owned(
            candidate, root=owned_root, ownership_authenticated=True,
            lifecycle_stage=lifecycle_stage, artifact_type="tombstone",
            recursive=candidate.is_dir(), tombstone=False))
    return tuple(results)
