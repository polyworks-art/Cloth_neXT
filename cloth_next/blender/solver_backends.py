# SPDX-License-Identifier: GPL-3.0-or-later
"""Single Blender-facing routing boundary for Cloth NeXt solver backends.

The Engine selector in Preferences decides the backend: GAIA bakes through
the PPF solver path (unchanged); AETHER is the native AETHER Engine
(``AETHER Engine.exe``), which replaces the upstream PPF build in testing
builds. Nothing here starts a process or touches a mesh.
"""

from __future__ import annotations

from ..simulation.backends import BackendId, backend_spec


def _preferences():
    import bpy
    from .addon_identity import addon_preferences
    try:
        return addon_preferences(bpy.context, __package__)
    except (KeyError, AttributeError, TypeError):
        return None


def active_backend_id(scene) -> BackendId:
    prefs = _preferences()
    if prefs is None:
        return BackendId.PPF
    try:
        engine = str(prefs.solver_engine)
    except (AttributeError, TypeError, ValueError):
        return BackendId.PPF
    return BackendId.PPF if engine == "GAIA" else BackendId.AETHER_ENGINE


def active_backend(scene):
    return backend_spec(active_backend_id(scene))


def unsupported_reason(scene) -> str:
    """Return one actionable cheap capability failure for the selected backend."""
    if active_backend_id(scene) is BackendId.AETHER_ENGINE:
        import bpy
        from . import aether_engine_backend
        return aether_engine_backend.unsupported_reason(bpy.context)
    return ""


def backend_status(context):
    """(ready, title, details) of the selected backend, or None for the PPF path."""
    if active_backend_id(context.scene) is not BackendId.AETHER_ENGINE:
        return None
    from . import aether_engine_backend
    status = aether_engine_backend.engine_status()
    return status.ready, status.title, ((status.detail,) if status.detail else ())


def backend_settings_key(context, base: str | None) -> str | None:
    """The settings fingerprint a cache of the selected backend carries."""
    if base is None or active_backend_id(context.scene) is not BackendId.AETHER_ENGINE:
        return base
    from ..aether_engine.bake import settings_fingerprint
    from .aether_engine_backend import quality_of
    return settings_fingerprint(base, quality_of(context.scene))


def begin_bake(context) -> tuple[str, bool]:
    if active_backend_id(getattr(context, "scene", None)) is BackendId.AETHER_ENGINE:
        from . import aether_engine_backend
        try:
            return aether_engine_backend.begin_bake(context)
        except aether_engine_backend.AetherSceneError as exc:
            from .solver_test import SceneValidationError
            raise SceneValidationError(f"{exc.code} {exc}".strip()) from exc
    from . import solver_test
    return solver_test.begin_production_bake(context)
