# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Product-facing solver release names.

Existing registries may still contain the upstream date-based display names.
This adapter resolves each registration against the bundled compatibility
manifest and presents its Cloth NeXt codename without changing executable
identity, compatibility, paths, ownership, or release metadata.
"""

from __future__ import annotations

from dataclasses import replace

from ..updater.solver_manifest import load_bundled_manifest
from ..updater.solver_registry import SolverRegistry
from . import preferences as _preferences

_PLATFORM = "windows-x86_64"
_ORIGINAL_READ_REGISTRY = getattr(
    _preferences._read_registry,
    "_clothnext_original_read_registry",
    _preferences._read_registry,
)


def _entry_for_installation(installation):
    manifest = load_bundled_manifest()
    releases = manifest.releases_for(_PLATFORM)

    if installation.official_release_tag:
        exact = next(
            (entry for entry in releases
             if entry.official_release_tag == installation.official_release_tag),
            None,
        )
        if exact is not None:
            return exact

    matches = tuple(
        entry for entry in releases
        if entry.protocol_version == installation.protocol_version
        and entry.schema_version == installation.schema_version
    )
    return matches[0] if len(matches) == 1 else None


def release_name(installation) -> str:
    """Return a verified codename, or preserve the stored fallback name."""
    entry = _entry_for_installation(installation)
    return entry.release_name if entry is not None else installation.display_name


def _read_registry_with_release_names() -> tuple[SolverRegistry, str | None]:
    registry, error = _ORIGINAL_READ_REGISTRY()
    renamed = tuple(
        replace(installation, display_name=release_name(installation))
        for installation in registry.installations
    )
    return replace(registry, installations=renamed), error


_read_registry_with_release_names._clothnext_original_read_registry = (
    _ORIGINAL_READ_REGISTRY
)


def install() -> None:
    """Present codenames through every preferences registry read."""
    _preferences._read_registry = _read_registry_with_release_names


def uninstall() -> None:
    """Restore the original registry reader for clean reload cycles."""
    if _preferences._read_registry is _read_registry_with_release_names:
        _preferences._read_registry = _ORIGINAL_READ_REGISTRY
