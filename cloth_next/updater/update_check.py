# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Central, pure solver-update decision for managed installations.

The installed identity of a managed installation is the immutable official
release (release tag plus asset SHA-256), never the internal solver package
version alone: multiple official releases may report the same internal
package version. The internal package, protocol, and schema versions remain
mandatory checks of the actually downloaded executable — they just no longer
decide whether an update exists.

Everything here is local and side-effect free: no network requests, no
threads, no process starts, no file mutation. Comparing the bundled manifest
with ``current.json`` is enough to decide at session initialization.
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from .install_paths import ActiveInstallation
from .solver_manifest import SolverCompatibilityEntry

_log = logging.getLogger(__name__)


class UpdateDecision(Enum):
    NOT_INSTALLED = auto()
    UP_TO_DATE = auto()
    #: The manifest pins a different immutable official release.
    UPDATE_AVAILABLE = auto()
    #: Legacy metadata without a known official release identity: the exact
    #: installed release is unknown, so the manifest-pinned release is
    #: offered as a compatible update.
    LEGACY_UPDATE_AVAILABLE = auto()
    #: Same official release tag but a different asset hash. A published
    #: official release is immutable, so this is a manifest or integrity
    #: problem, never a silent, normal release switch. The manifest is the
    #: reviewed, trusted source; installing it is offered, but the conflict
    #: is logged loudly for diagnosis.
    IDENTITY_CONFLICT = auto()


def evaluate_update(active: ActiveInstallation | None,
                    entry: SolverCompatibilityEntry | None) -> UpdateDecision:
    """Decide locally whether the manifest-pinned release is an update."""
    if active is None:
        return UpdateDecision.NOT_INSTALLED
    if entry is None:
        # No verified download exists; never claim an update is available.
        return UpdateDecision.UP_TO_DATE
    if not active.has_release_identity:
        return UpdateDecision.LEGACY_UPDATE_AVAILABLE
    if active.official_release_tag != entry.official_release_tag:
        return UpdateDecision.UPDATE_AVAILABLE
    if active.asset_sha256 != entry.sha256:
        _log.warning(
            "solver release %s is installed with asset hash %s but the "
            "compatibility manifest pins hash %s for the same immutable "
            "release tag; treating this as a manifest/integrity problem and "
            "offering a verified reinstall",
            active.official_release_tag, active.asset_sha256, entry.sha256)
        return UpdateDecision.IDENTITY_CONFLICT
    return UpdateDecision.UP_TO_DATE


_AVAILABLE = frozenset({UpdateDecision.UPDATE_AVAILABLE,
                        UpdateDecision.LEGACY_UPDATE_AVAILABLE,
                        UpdateDecision.IDENTITY_CONFLICT})


def solver_update_available(active: ActiveInstallation | None,
                            entry: SolverCompatibilityEntry | None) -> bool:
    """True when a managed installation should be offered the pinned release."""
    return evaluate_update(active, entry) in _AVAILABLE
