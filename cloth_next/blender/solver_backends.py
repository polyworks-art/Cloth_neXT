# SPDX-License-Identifier: GPL-3.0-or-later
"""Single Blender-facing routing boundary for Cloth NeXt solver backends."""

from __future__ import annotations

from ..simulation.backends import BackendId, backend_spec


def active_backend_id(scene) -> BackendId:
    return BackendId.PPF


def active_backend(scene):
    return backend_spec(active_backend_id(scene))


def unsupported_reason(scene) -> str:
    """Return one actionable cheap capability failure for the selected backend."""
    return ""


def begin_bake(context) -> tuple[str, bool]:
    from . import solver_test
    return solver_test.begin_production_bake(context)
