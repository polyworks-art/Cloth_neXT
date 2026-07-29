# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resolve add-on preferences in source and Blender Extension namespaces.

Blender may expose an installed extension as ``bl_ext.<repo>.cloth_next``
while source-tree tests and a few background contexts use the manifest id
``cloth_next``.  Runtime code must accept both without silently switching
channels or losing user preferences.
"""

from __future__ import annotations

from ..updater.addon_updates import EXTENSION_ID


def package_addon_id(package_name: str) -> str:
    """Return the package root used by ``AddonPreferences.bl_idname``."""
    return str(package_name).partition(".blender")[0]


def addon_id_candidates(package_name: str) -> tuple[str, ...]:
    """Ordered preference keys for installed-extension and source contexts."""
    package_id = package_addon_id(package_name)
    values = (package_id, package_id.rsplit(".", 1)[-1], EXTENSION_ID)
    return tuple(dict.fromkeys(value for value in values if value))


def addon_preferences(context, package_name: str):
    """Return Cloth NeXt preferences or raise ``KeyError`` when unavailable."""
    addons = context.preferences.addons
    for addon_id in addon_id_candidates(package_name):
        try:
            addon = addons[addon_id]
        except (KeyError, TypeError):
            continue
        preferences = getattr(addon, "preferences", None)
        if preferences is not None:
            return preferences
    raise KeyError(
        "Cloth NeXt preferences are unavailable for "
        f"{addon_id_candidates(package_name)!r}")
