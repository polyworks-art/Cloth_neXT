"""Blender extension entry point for Cloth NeXt."""

from __future__ import annotations

__all__ = ["register", "unregister"]
__version__ = "0.1.0"


def register() -> None:
    from .blender.registration import register as register_blender
    register_blender()


def unregister() -> None:
    from .blender.registration import unregister as unregister_blender
    unregister_blender()
