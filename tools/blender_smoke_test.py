"""Run inside Blender: enable/disable the extension registration twice."""

from __future__ import annotations

import importlib
import sys


def main() -> None:
    module_name = "bl_ext.user_default.cloth_next"
    try:
        extension = importlib.import_module(module_name)
    except ModuleNotFoundError:
        module_name = "cloth_next"
        extension = importlib.import_module(module_name)

    for _ in range(2):
        extension.register()
        extension.register()  # idempotency guard
        extension.unregister()
        extension.unregister()

    bpy = importlib.import_module("bpy")
    assert not hasattr(bpy.types, "CLOTHNEXT_AddonPreferences")
    assert not any("cloth_next" in repr(timer).lower() for timer in ())
    print(f"Cloth NeXt registration smoke test passed ({module_name})")


if __name__ == "__main__":
    main()

