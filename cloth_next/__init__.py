# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Blender extension entry point for Cloth NeXt.

The canonical version lives exclusively in ``blender_manifest.toml``
(docs/RELEASE_POLICY.md section 2); use :func:`manifest_version` to read it.
"""

from __future__ import annotations

__all__ = ["register", "unregister", "manifest_version"]


def manifest_version() -> str:
    """Read the single canonical version from blender_manifest.toml."""
    import tomllib
    from pathlib import Path
    manifest = Path(__file__).resolve().parent / "blender_manifest.toml"
    return tomllib.loads(manifest.read_text(encoding="utf-8"))["version"]


def register() -> None:
    from .blender.registration import register as register_blender
    register_blender()


def unregister() -> None:
    from .blender.registration import unregister as unregister_blender
    unregister_blender()
