# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run inside Blender: real registration smoke test under the bl_ext namespace.

Enables/disables the extension registration twice, verifies idempotency, and
verifies that unregister leaves no classes, no installer worker threads, no
``Object.cloth_next`` pointer, no Physics-Add draw callback, and no leaked
state behind. Also exercises the Phase 2.8 add/remove physics operators on a
real mesh object.
"""

from __future__ import annotations

import importlib
import threading


def _clothnext_draw_callback_count(bpy) -> int:
    draw = getattr(bpy.types.PHYSICS_PT_add, "draw", None)
    funcs = getattr(draw, "_draw_funcs", ())
    return sum(1 for func in funcs
               if getattr(func, "_clothnext_add_entry", False))


def _phase28_roundtrip(bpy) -> None:
    """Enable and remove Cloth NeXt on a real mesh through the operators."""
    mesh_obj = next((obj for obj in bpy.data.objects if obj.type == "MESH"), None)
    if mesh_obj is None:
        bpy.ops.mesh.primitive_plane_add()
        mesh_obj = bpy.context.active_object
    bpy.context.view_layer.objects.active = mesh_obj
    modifier_count = len(mesh_obj.modifiers)

    assert not mesh_obj.cloth_next.enabled
    bpy.ops.clothnext.add_physics()
    assert mesh_obj.cloth_next.enabled
    assert mesh_obj.cloth_next.role == "CLOTH"
    # no native Cloth modifier and no other modifier appears
    assert len(mesh_obj.modifiers) == modifier_count
    assert not any(mod.type == "CLOTH" for mod in mesh_obj.modifiers)

    bpy.ops.clothnext.remove_physics()
    assert not mesh_obj.cloth_next.enabled
    assert mesh_obj.cloth_next.role == "CLOTH"


def _solver_download_dispatch_check(bpy, module_name: str) -> None:
    """The download button regression: RNA must resolve the operator classes
    (a registered-operator subclass used to corrupt this mapping and silently
    skip ``invoke``), and the confirmation state machine must work. Declining
    the confirmation performs no network or file operation.
    """
    preferences = importlib.import_module(module_name + ".blender.preferences")
    for name in ("CLOTHNEXT_OT_solver_download", "CLOTHNEXT_OT_solver_repair"):
        resolved = bpy.types.Operator.bl_rna_get_subclass_py(name)
        assert resolved is getattr(preferences, name), (
            f"RNA does not resolve {name} to its Python class")
    installer = preferences._session.ensure_installer()
    assert installer is not None, preferences._session.disabled_reason
    state = installer.request_download()
    assert state.name == "AWAITING_CONFIRMATION"
    state = installer.install(confirmed=False)  # decline: no download starts
    assert state.name == "DOWNLOAD_AVAILABLE"


def main() -> None:
    import bpy

    module_name = None
    for candidate in ("bl_ext.user_default.cloth_next", "cloth_next"):
        try:
            extension = importlib.import_module(candidate)
            module_name = candidate
            break
        except ModuleNotFoundError:
            continue
    if module_name is None:
        raise SystemExit("cloth_next is not importable (bl_ext or source path)")

    for _ in range(2):
        extension.register()
        extension.register()  # idempotency guard
        assert hasattr(bpy.types, "CLOTHNEXT_AddonPreferences")
        assert hasattr(bpy.types, "CLOTHNEXT_PG_object_settings")
        assert hasattr(bpy.types, "CLOTHNEXT_OT_add_physics")
        assert hasattr(bpy.types, "CLOTHNEXT_OT_remove_physics")
        assert hasattr(bpy.types, "CLOTHNEXT_PT_physics")
        assert "cloth_next" in bpy.types.Object.bl_rna.properties
        assert _clothnext_draw_callback_count(bpy) == 1
        _solver_download_dispatch_check(bpy, module_name)
        _phase28_roundtrip(bpy)
        extension.unregister()
        extension.unregister()
        assert not hasattr(bpy.types, "CLOTHNEXT_AddonPreferences")
        assert not hasattr(bpy.types, "CLOTHNEXT_PT_physics")
        assert "cloth_next" not in bpy.types.Object.bl_rna.properties
        assert _clothnext_draw_callback_count(bpy) == 0

    leftover = [thread.name for thread in threading.enumerate()
                if thread.name.startswith("clothnext-")]
    assert not leftover, f"installer worker threads survived unregister: {leftover}"
    assert extension.manifest_version()
    print(f"Cloth NeXt registration smoke test passed ({module_name}, "
          f"version {extension.manifest_version()})")


if __name__ == "__main__":
    main()
