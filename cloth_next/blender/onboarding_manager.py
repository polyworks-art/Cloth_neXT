# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lifecycle-safe one-shot launch of Companion onboarding screens."""
from __future__ import annotations

import os
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import secrets

import bpy

from .. import manifest_version
from ..bake.companion_bundle import validate_bundle
from ..onboarding import SeenState, load_welcome, load_whats_new
from .addon_identity import addon_preferences

_START_DELAY_SECONDS = 1.25
LOG = logging.getLogger(__name__)
_stale_timers = globals().get("_stale_timers", []) + [
    callback for name in ("_startup_pulse", "_poll_startup")
    if callable(callback := globals().get(name))]
_pending = globals().get("_pending", [])


def _preferences():
    return addon_preferences(bpy.context, __package__)


def _state(preferences=None) -> SeenState:
    preferences = preferences or _preferences()
    return SeenState.from_json(getattr(preferences, "onboarding_state", ""))


def _write_state(value: SeenState, preferences=None) -> None:
    preferences = preferences or _preferences()
    preferences.onboarding_state = value.to_json()


def companion_info_command(mode: str, version: str | None = None) -> list[str]:
    extension_root = Path(__file__).resolve().parents[1]
    repository_root = extension_root.parent
    try:
        command = [str(validate_bundle(extension_root, manifest_version()))]
    except (OSError, ValueError, KeyError):
        if (os.environ.get("CLOTH_NEXT_DEVELOPER_COMPANION") != "1"
                or not (repository_root / "companion" / "app.py").is_file()):
            raise
        python = os.environ.get("CLOTH_NEXT_COMPANION_PYTHON", sys.executable)
        command = [python, "-m", "companion.app"]
    command += ["--mode", mode]
    command += ["--content-root", str(
        extension_root / "resources" / "onboarding")]
    if version is not None:
        command += ["--version", version]
    return command


def launch_screen(mode: str, *, manual: bool = False) -> tuple[bool, str]:
    """Start one independent informational window; never block Blender."""
    version = manifest_version()
    temporary = None
    try:
        if mode == "welcome":
            load_welcome()
        elif mode == "whats-new":
            load_whats_new(version)
        else:
            raise ValueError(f"unknown Companion screen: {mode}")
        command = companion_info_command(
            mode, version if mode == "whats-new" else None)
        temporary = tempfile.TemporaryDirectory(prefix="clothnext-onboarding-")
        ready = Path(temporary.name) / "ready"
        token = secrets.token_hex(16)
        env = dict(os.environ, CLOTH_NEXT_INFO_READY_PATH=str(ready),
                   CLOTH_NEXT_INFO_READY_TOKEN=token)
        process = subprocess.Popen(
            command, cwd=Path(__file__).resolve().parents[2], shell=False, env=env)
    except (OSError, ValueError, KeyError) as exc:
        if temporary is not None:
            temporary.cleanup()
        return False, f"Could not open Cloth NeXt {mode}: {exc}"
    if process.poll() is not None:
        temporary.cleanup()
        return False, f"Cloth NeXt {mode} exited during startup."
    _pending.append((process, temporary, ready, token, mode, version, manual,
                     time.monotonic() + 20))
    if not bpy.app.timers.is_registered(_poll_startup):
        bpy.app.timers.register(_poll_startup, first_interval=0.1)
    return True, "Welcome opened" if mode == "welcome" else "What's New opened"


def _poll_startup():
    for item in tuple(_pending):
        process, temporary, ready, token, mode, version, manual, deadline = item
        try:
            acknowledged = ready.is_file() and ready.read_text(encoding="utf-8") == token
        except OSError:
            acknowledged = False
        if acknowledged or process.poll() is not None or time.monotonic() >= deadline:
            try:
                if acknowledged and not manual:
                    preferences = _preferences()
                    _write_state(_state(preferences).mark_seen(mode, version), preferences)
                elif not acknowledged and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=2)
            finally:
                temporary.cleanup()
                _pending.remove(item)
    return 0.1 if _pending else None


def _startup_pulse() -> None:
    """One-shot callback holding no Blender context or RNA references."""
    if getattr(bpy.app, "background", False) or _pending:
        return None
    try:
        screen = _state().next_screen(manifest_version())
        if screen:
            ok, message = launch_screen(screen)
            if not ok:
                LOG.warning("Cloth NeXt onboarding launch skipped: %s", message)
    except (KeyError, AttributeError, OSError, ValueError) as exc:
        # Onboarding is optional at runtime; an invalid/missing Companion must
        # never prevent Blender or the add-on from starting.
        LOG.warning("Cloth NeXt onboarding is unavailable: %s", exc)
    return None


def register() -> None:
    for callback in _stale_timers:
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    _stale_timers.clear()
    if _pending and not bpy.app.timers.is_registered(_poll_startup):
        bpy.app.timers.register(_poll_startup, first_interval=0.1)
    if not getattr(bpy.app, "background", False) and not bpy.app.timers.is_registered(
            _startup_pulse):
        bpy.app.timers.register(_startup_pulse, first_interval=_START_DELAY_SECONDS)


def unregister() -> None:
    for callback in (*_stale_timers, _startup_pulse, _poll_startup):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    _stale_timers.clear()
    for item in tuple(_pending):
        process, temporary = item[:2]
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
        finally:
            temporary.cleanup()
            _pending.remove(item)


class CLOTHNEXT_OT_open_welcome(bpy.types.Operator):
    bl_idname = "clothnext.open_welcome"
    bl_label = "Open Welcome"
    bl_description = "Open the Cloth NeXt getting-started screen"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        ok, message = launch_screen("welcome", manual=True)
        self.report({"INFO" if ok else "ERROR"}, message)
        return {"FINISHED" if ok else "CANCELLED"}


class CLOTHNEXT_OT_open_whats_new(bpy.types.Operator):
    bl_idname = "clothnext.open_whats_new"
    bl_label = "What's New"
    bl_description = "Open What's New for this installed Cloth NeXt version"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        ok, message = launch_screen("whats-new", manual=True)
        self.report({"INFO" if ok else "ERROR"}, message)
        return {"FINISHED" if ok else "CANCELLED"}


CLASSES = (CLOTHNEXT_OT_open_welcome, CLOTHNEXT_OT_open_whats_new)
