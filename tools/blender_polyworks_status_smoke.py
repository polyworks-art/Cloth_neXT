# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Run inside real Blender: the PolyWorks status integration is read-only.

blender --background --factory-startup --python-exit-code 1 \
    --python tools/blender_polyworks_status_smoke.py [-- --source]

Checks registration/reload/unregister timer hygiene, that Preferences have no
account properties, that only the Companion launcher operator exists, that the
status reader works with Blender's Python (dependency-free Ed25519 path), that
no API/vault module is imported, and that a status tick stays far below a frame.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import bpy

ACCOUNT_ROOT = Path(tempfile.mkdtemp(prefix="clothnext-polyworks-smoke-"))
os.environ["CLOTH_NEXT_ACCOUNT_ROOT"] = str(ACCOUNT_ROOT)


def _load():
    if "--source" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        return importlib.import_module("cloth_next")
    for name in ("bl_ext.user_default.cloth_next", "cloth_next"):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            pass
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    return importlib.import_module("cloth_next")


def _check(condition, message):
    if not condition:
        raise SystemExit("PolyWorks smoke failed: " + message)


def main():
    package = _load()
    name = package.__name__
    if not getattr(sys.modules.get(f"{name}.blender.registration"), "_registered", False):
        package.register()
    status = sys.modules[f"{name}.blender.account_status"]
    _check(bpy.app.timers.is_registered(status._tick), "status timer not registered")
    _check(hasattr(bpy.types, "CLOTHNEXT_OT_open_account_companion"), "launcher missing")
    preferences = sys.modules[f"{name}.blender.preferences"].CLOTHNEXT_AddonPreferences
    words = ("email", "password", "token", "licen", "account", "api", "activation", "seat")
    names = [p.identifier for p in preferences.bl_rna.properties]
    _check(not [n for n in names if any(w in n.lower() for w in words)],
           f"account-like preference properties: {names}")
    # Reload keeps exactly one timer.
    package.unregister()
    _check(not bpy.app.timers.is_registered(status._tick), "timer leaked after unregister")
    package.register()
    status = sys.modules[f"{name}.blender.account_status"]
    _check(bpy.app.timers.is_registered(status._tick), "timer missing after re-register")

    # Published state is read on a worker thread; the tick itself is cheap.
    storage = importlib.import_module(f"{name}.polyworks.storage")
    store = storage.LocalStore(ACCOUNT_ROOT)
    store.installation_id()
    store.write_json("state.json", {"account": {"signed_in": True, "session": "active",
                                                "email": "smoke@example.com"},
                                    "licenses": [], "connection": {"online": True}})
    started = time.perf_counter()
    status._tick()
    tick_ms = (time.perf_counter() - started) * 1000
    status._state["worker"].join(timeout=5)
    view = status.current_view()
    _check(view is not None and view.status == "NO_LICENSE", f"unexpected view {view}")
    _check(tick_ms < 50, f"status tick took {tick_ms:.1f} ms")
    # A forged entitlement is never accepted, with or without `cryptography`.
    store.write_entitlement("eyJ0eXAiOiJwb2x5d29ya3MuZW50aXRsZW1lbnQifQ.AAAA")
    status._tick()
    status._state["worker"].join(timeout=5)
    _check(status.current_view().status == "INVALID" and not status.authorized(),
           "forged entitlement was not rejected")
    ed25519 = importlib.import_module(f"{name}.polyworks.ed25519")
    vector = ("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
              "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
              "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    _check(ed25519.verify(bytes.fromhex(vector[0]), b"", bytes.fromhex(vector[1]),
                          use_provider=False), "reference Ed25519 failed in Blender's Python")
    forbidden = [m for m in (f"{name}.polyworks.api", f"{name}.polyworks.service",
                             f"{name}.polyworks.vault") if m in sys.modules]
    _check(not forbidden, f"Blender imported Companion-only modules: {forbidden}")
    package.unregister()
    _check(status._state["worker"] is None, "worker thread reference leaked")
    print(json.dumps({"polyworks_smoke": "passed", "tick_ms": round(tick_ms, 2),
                      "cryptography": "cryptography" in sys.modules}))


main()
