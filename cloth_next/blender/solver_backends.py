# SPDX-License-Identifier: GPL-3.0-or-later
"""Single Blender-facing routing boundary for Cloth NeXt solver backends."""

from __future__ import annotations

from ..simulation.backends import BackendId, backend_spec, default_backend


def active_backend_id(scene) -> BackendId:
    settings = getattr(scene, "cloth_next_solver", None)
    value = str(getattr(settings, "backend", "") or "")
    if not value:
        legacy = getattr(scene, "cloth_next_newton_preview", None)
        value = str(getattr(legacy, "bake_backend", "") or "")
    try:
        return BackendId(value)
    except ValueError:
        return default_backend()


def active_backend(scene):
    return backend_spec(active_backend_id(scene))


def unsupported_reason(scene) -> str:
    """Return one actionable cheap capability failure for the selected backend."""
    if active_backend_id(scene) is BackendId.PPF:
        return ""
    enabled = tuple(obj for obj in getattr(scene, "objects", ())
                    if bool(getattr(getattr(obj, "cloth_next", None),
                                    "enabled", False)))
    unsupported_roles = sorted({str(obj.cloth_next.role) for obj in enabled
                                if str(obj.cloth_next.role) not in
                                {"CLOTH", "SOFT_BODY", "RIGID_BODY",
                                 "COLLIDER", "FORCE"}})
    if unsupported_roles:
        roles = ", ".join(role.replace("_", " ").title()
                          for role in unsupported_roles)
        return (f"Newton does not currently support these Cloth NeXt roles: {roles}. "
                "Switch to PPF for this scene.")
    for obj in enabled:
        if str(obj.cloth_next.role) != "CLOTH":
            continue
        if bool(obj.cloth_next.pressure.enable_inflate):
            return ("Newton cannot currently reproduce Pressure. Disable Pressure "
                    "or switch to PPF.")
        if bool(obj.cloth_next.pressure.sewing_enabled):
            return ("Newton cannot currently reproduce Sewing. Disable Sewing "
                    "or switch to PPF.")
    forces = sorted({str(obj.cloth_next.force.force_type) for obj in enabled
                     if str(obj.cloth_next.role) == "FORCE"
                     and str(obj.cloth_next.force.force_type) != "GRAVITY"})
    if forces:
        return ("Newton currently supports only Gravity forces. Disable "
                + ", ".join(forces) + " or switch to PPF.")
    return ""


def begin_bake(context) -> tuple[str, bool]:
    identifier = active_backend_id(getattr(context, "scene", None))
    if identifier is BackendId.NEWTON:
        from . import newton_bake
        return newton_bake.begin(context)
    from . import solver_test
    return solver_test.begin_production_bake(context)


def request_cancel() -> bool:
    from . import newton_bake
    return newton_bake.request_cancel()


def newton_installation_status():
    from . import newton_preview
    return newton_preview.newton_installation_status()
