# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Central, fail-closed render eligibility based on authenticated playback."""

from __future__ import annotations
from pathlib import Path
import bpy

from ..bake import cache_metadata
from .playback_cache import has_cloth_next_playback_marker

DEFORMABLE_ROLES = frozenset({"CLOTH", "ROD", "SOFT_BODY"})


def _cache_path(obj):
    if getattr(obj, "type", "") == "CURVE":
        value = getattr(getattr(obj, "data", None), "get", lambda *_: "")(
            "cloth_next_rod_cache", ""
        )
        return Path(value) if value else None
    modifier = next(
        (
            item
            for item in getattr(obj, "modifiers", ())
            if bool(getattr(item, "show_render", True))
            and has_cloth_next_playback_marker(obj, item)
        ),
        None,
    )
    value = getattr(modifier, "filepath", "") if modifier is not None else ""
    return Path(bpy.path.abspath(value)) if value else None


def should_threadmark_render(scene) -> bool:
    """True only when an active deformable has an authenticated current cache."""
    for obj in getattr(scene, "objects", ()):
        settings = getattr(obj, "cloth_next", None)
        if (
            settings is None
            or not bool(getattr(settings, "enabled", False))
            or str(getattr(settings, "role", "")) not in DEFORMABLE_ROLES
            or bool(getattr(obj, "hide_render", False))
            or int(getattr(settings, "baked_fingerprint_version", 0)) <= 0
        ):
            continue
        settings_fp = str(getattr(settings, "baked_settings_fingerprint", "") or "")
        geometry_fp = str(getattr(settings, "baked_geometry_fingerprint", "") or "")
        path = _cache_path(obj)
        if path is None or not settings_fp or not geometry_fp:
            continue
        inspection = cache_metadata.inspect_cache(
            path, settings_fingerprint=settings_fp, geometry_fingerprint=geometry_fp
        )
        if inspection.usable:
            return True
    return False
