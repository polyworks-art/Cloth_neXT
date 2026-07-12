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
import sys
import threading
from pathlib import Path


def _load_extension():
    """Import the extension and report how: enabled bl_ext module, explicit
    enable, or the source checkout.

    The source fallback matters on Linux CI: the extension is Windows-only
    (``platforms = ["windows-x64"]``), so Blender on Linux installs it but
    never enables it as a ``bl_ext`` module; registration itself is
    platform-independent and is still fully exercised from the source tree.
    """
    errors: list[str] = []
    for candidate in ("bl_ext.user_default.cloth_next", "cloth_next"):
        try:
            return importlib.import_module(candidate), candidate
        except ModuleNotFoundError as exc:
            errors.append(f"{candidate}: {exc}")
    try:
        import addon_utils
        module = addon_utils.enable("bl_ext.user_default.cloth_next",
                                    default_set=False)
        if module is not None:
            return module, "bl_ext.user_default.cloth_next (enabled by smoke test)"
        errors.append("addon_utils.enable(bl_ext.user_default.cloth_next) "
                      "returned None")
    except Exception as exc:  # noqa: BLE001 — diagnostics only
        errors.append(f"addon_utils.enable: {exc}")
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        return importlib.import_module("cloth_next"), f"source tree {repo_root}"
    except ModuleNotFoundError as exc:
        errors.append(f"source tree {repo_root}: {exc}")
    raise SystemExit("cloth_next is not importable:\n  " + "\n  ".join(errors))


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
    if installer.state.name in {"NOT_INSTALLED", "DOWNLOAD_AVAILABLE"}:
        state = installer.request_download()
        assert state.name == "AWAITING_CONFIRMATION"
        state = installer.install(confirmed=False)  # decline: no download starts
        assert state.name == "DOWNLOAD_AVAILABLE"
    else:
        # A solver is already installed on this machine; the roundtrip only
        # applies to the not-installed flow (which CI always exercises).
        print(f"solver installer is {installer.state.name}; "
              "skipping the confirmation roundtrip")


def _addon_update_section_check(bpy, module_name: str) -> None:
    """Phase 2.8B add-on update workflow: RNA dispatch, no auto side effects,
    no duplicate repositories, correct public-API polls, clean state."""
    updates = importlib.import_module(module_name + ".blender.addon_update_operators")
    for cls in updates.CLASSES:
        resolved = bpy.types.Operator.bl_rna_get_subclass_py(cls.__name__)
        assert resolved is cls, f"RNA does not resolve {cls.__name__}"
    # no automatic check, no repository was created merely by registering
    assert updates.session().state.name == "NOT_CHECKED"
    repo_urls = [getattr(repo, "remote_url", "")
                 for repo in bpy.context.preferences.extensions.repos]
    for url in repo_urls:
        assert repo_urls.count(url) == 1 or not url, f"duplicate repository {url}"
    # the public operators this feature relies on exist in this Blender
    assert hasattr(bpy.ops.extensions, "repo_sync")
    assert hasattr(bpy.ops.extensions, "package_upgrade_all")
    assert hasattr(bpy.ops.extensions, "userpref_show_for_update")
    assert hasattr(bpy.ops.preferences, "extension_repo_add")
    assert not bpy.app.timers.is_registered(updates._ui_refresh_pulse)


def main() -> None:
    import bpy

    extension, import_origin = _load_extension()
    module_name = extension.__name__
    print(f"Cloth NeXt smoke test: importing via {import_origin}")

    # `cls.is_registered` is the reliable signal: AddonPreferences classes are
    # not exposed as `bpy.types.<ClassName>` attributes in Blender 5.x.
    blender_package = importlib.import_module(module_name + ".blender")
    classes = []
    for submodule in ("preferences", "addon_update_operators", "object_properties",
                      "physics_operators", "bake_operators", "physics_ui"):
        loaded = importlib.import_module(f"{blender_package.__name__}.{submodule}")
        classes.extend(loaded.CLASSES)

    for _ in range(2):
        extension.register()
        extension.register()  # idempotency guard
        for cls in classes:
            assert cls.is_registered, f"{cls.__name__} is not registered"
        assert "cloth_next" in bpy.types.Object.bl_rna.properties
        assert _clothnext_draw_callback_count(bpy) == 1
        _solver_download_dispatch_check(bpy, module_name)
        _addon_update_section_check(bpy, module_name)
        _phase28_roundtrip(bpy)
        icons = importlib.import_module(module_name + ".blender.icon_registry")
        hud = importlib.import_module(module_name + ".blender.hud")
        physics_ui = importlib.import_module(module_name + ".blender.physics_ui")
        assert icons._collection is not None and "bake" in icons._collection, \
            "croissant runtime preview was not loaded"
        assert hud._handle is not None, "HUD handler was not installed"
        obj = bpy.context.active_object
        obj.cloth_next.enabled = True
        cloth_only = (physics_ui.CLOTHNEXT_PT_quality,
                      physics_ui.CLOTHNEXT_PT_physical,
                      physics_ui.CLOTHNEXT_PT_damping,
                      physics_ui.CLOTHNEXT_PT_pressure,
                      physics_ui.CLOTHNEXT_PT_shape)
        obj.cloth_next.role = "CLOTH"
        assert all(panel.poll(bpy.context) for panel in cloth_only)
        obj.cloth_next.role = "COLLIDER"
        assert not any(panel.poll(bpy.context) for panel in cloth_only)
        assert physics_ui.CLOTHNEXT_PT_collisions.poll(bpy.context)
        obj.cloth_next.enabled = False
        extension.unregister()
        extension.unregister()
        for cls in classes:
            assert not cls.is_registered, f"{cls.__name__} survived unregister"
        assert "cloth_next" not in bpy.types.Object.bl_rna.properties
        assert _clothnext_draw_callback_count(bpy) == 0
        assert icons._collection is None and hud._handle is None
        updates = importlib.import_module(
            module_name + ".blender.addon_update_operators")
        assert not bpy.app.timers.is_registered(updates._ui_refresh_pulse)

    leftover = [thread.name for thread in threading.enumerate()
                if thread.name.startswith("clothnext-")]
    assert not leftover, f"installer worker threads survived unregister: {leftover}"
    assert extension.manifest_version()
    print(f"Cloth NeXt registration smoke test passed ({module_name}, "
          f"version {extension.manifest_version()})")


if __name__ == "__main__":
    main()
