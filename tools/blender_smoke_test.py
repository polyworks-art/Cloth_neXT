# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run inside Blender: real registration smoke test under the bl_ext namespace.

Enables/disables the extension registration twice, verifies idempotency, and
verifies that unregister leaves no classes, no installer worker threads, and
no leaked state behind.
"""

from __future__ import annotations

import importlib
import threading


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
        extension.unregister()
        extension.unregister()
        assert not hasattr(bpy.types, "CLOTHNEXT_AddonPreferences")

    leftover = [thread.name for thread in threading.enumerate()
                if thread.name.startswith("clothnext-")]
    assert not leftover, f"installer worker threads survived unregister: {leftover}"
    assert extension.manifest_version()
    print(f"Cloth NeXt registration smoke test passed ({module_name}, "
          f"version {extension.manifest_version()})")


if __name__ == "__main__":
    main()
