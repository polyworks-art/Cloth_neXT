# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lifecycle-safe one-shot launch of Companion onboarding screens."""
from __future__ import annotations

import os
import logging
from pathlib import Path
import subprocess
import sys

import bpy

from .. import manifest_version
from ..bake.companion_bundle import validate_bundle
from ..onboarding import SeenState, load_welcome, load_whats_new
from .addon_identity import addon_preferences

_START_DELAY_SECONDS = 1.25
LOG = logging.getLogger(__name__)


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
    try:
        if mode == "welcome":
            load_welcome()
        elif mode == "whats-new":
            load_whats_new(version)
        else:
            raise ValueError(f"unknown Companion screen: {mode}")
        command = companion_info_command(
            mode, version if mode == "whats-new" else None)
        process = subprocess.Popen(
            command, cwd=Path(__file__).resolve().parents[2], shell=False)
    except (OSError, ValueError, KeyError) as exc:
        return False, f"Could not open Cloth NeXt {mode}: {exc}"
    if process.poll() is not None:
        return False, f"Cloth NeXt {mode} exited during startup."
    if not manual:
        preferences = _preferences()
        _write_state(_state(preferences).mark_seen(mode, version), preferences)
    return True, "Welcome opened" if mode == "welcome" else "What's New opened"


def _startup_pulse() -> None:
    """One-shot callback holding no Blender context or RNA references."""
    if getattr(bpy.app, "background", False):
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
    if not getattr(bpy.app, "background", False) and not bpy.app.timers.is_registered(
            _startup_pulse):
        bpy.app.timers.register(_startup_pulse, first_interval=_START_DELAY_SECONDS)


def unregister() -> None:
    if bpy.app.timers.is_registered(_startup_pulse):
        bpy.app.timers.unregister(_startup_pulse)


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
