# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only PolyWorks account and services status inside Blender.

Optional: Cloth NeXt Classic is licensed through Superhive; nothing here gates
simulation or Bake.

Blender never contacts the PolyWorks API, never opens the session vault and
never changes account or licence state. A persistent timer stats the
Companion-published files every few seconds (no reads in draw callbacks); on a
change, one short-lived worker thread reads them and verifies the signed
entitlement locally. On Windows a resident background Companion
(``--mode account-tray``, no console, tray icon) does the check-ins and opens
the account window from its tray icon; elsewhere renewal is a headless run
(``--mode account-sync``) that Blender may launch when a check-in is due and
no Bake is active.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import bpy

from ..polyworks.status import StatusView, file_signature, read_view
from ..polyworks.storage import LocalStore
from ..polyworks.trust import load_trust

LOG = logging.getLogger(__name__)
POLL_SECONDS = 2.0
FULL_REFRESH_SECONDS = 30.0
SYNC_START_DELAY_SECONDS = 8.0
SYNC_MIN_SPACING_SECONDS = 600.0
SYNC_TIMEOUT_SECONDS = 120.0
TRAY_RETRY_SECONDS = 60.0
TRAY = sys.platform == "win32"
_HIDDEN = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
           "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}

_stale_timers = globals().get("_stale_timers", []) + [
    callback for name in ("_tick",) if callable(callback := globals().get(name))]
_state = {
    "view": None, "signature": None, "last_full": 0.0, "generation": 0,
    "worker": None, "redraw": False, "registered_at": 0.0, "last_sync": None,
    "sync_process": None, "sync_started": 0.0, "windows": [],
    "tray_process": None, "tray_attempt": None,
}


def current_view() -> StatusView | None:
    """Last computed view; None until the first background read finished."""
    return _state["view"]


def authorized() -> bool:
    """True only for an entitlement Blender verified itself.

    Cloth NeXt currently has no feature gated by this (PolyWorks services are
    not released yet); simulation and Bake never consult it (contract 4.3a).
    """
    view = _state["view"]
    return bool(view and view.authorized)


def _compute(generation: int, signature) -> None:
    try:
        view = read_view(LocalStore(), trust=load_trust())
    except Exception as exc:  # status display must never break Blender
        LOG.warning("PolyWorks status could not be read: %s", type(exc).__name__)
        return
    if generation == _state["generation"]:
        _state.update(view=view, signature=signature, redraw=True)


def _start_read(signature) -> None:
    worker = _state["worker"]
    if worker is not None and worker.is_alive():
        return
    _state["last_full"] = time.monotonic()
    worker = threading.Thread(target=_compute, args=(_state["generation"], signature),
                              name="ClothNeXtAccountStatus", daemon=True)
    _state["worker"] = worker
    worker.start()


def _tag_redraw() -> None:
    _state["redraw"] = False
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "PREFERENCES":
                    area.tag_redraw()
    except (AttributeError, ReferenceError, RuntimeError):
        pass


def _bake_active() -> bool:
    try:
        from ..bake.controller import shared_controller
        return shared_controller.snapshot().active
    except Exception:
        return True  # unknown: never start account work


def _companion_command(mode: str) -> list[str]:
    from .onboarding_manager import companion_info_command
    command = companion_info_command(mode)
    version = getattr(bpy.app, "version_string", "")
    if version:
        command += ["--blender-version", version.split(" ")[0]]
    return command


def _reap() -> None:
    process = _state["sync_process"]
    if process is None:
        return
    if process.poll() is not None:
        _state["sync_process"] = None
        _state["signature"] = None  # re-read the published result
    elif time.monotonic() - _state["sync_started"] > SYNC_TIMEOUT_SECONDS:
        process.terminate()
    _state["windows"] = [p for p in _state["windows"] if p.poll() is None]


def _sync_due(view: StatusView | None, now: float) -> bool:
    if view is None or view.companion_running or not view.account_label.startswith("Signed in"):
        return False
    return view.next_check_at is None or view.next_check_at <= time.time()


def _maybe_sync() -> None:
    now = time.monotonic()
    if (getattr(bpy.app, "background", False) or _state["sync_process"] is not None
            or now - _state["registered_at"] < SYNC_START_DELAY_SECONDS
            or (_state["last_sync"] is not None and now - _state["last_sync"] < SYNC_MIN_SPACING_SECONDS)
            or not _sync_due(_state["view"], now) or _bake_active()):
        return
    _state["last_sync"] = now
    try:
        _state["sync_process"] = subprocess.Popen(
            _companion_command("account-sync"), cwd=Path(__file__).resolve().parents[2],
            shell=False, **_HIDDEN)
        _state["sync_started"] = now
    except (OSError, ValueError, KeyError) as exc:
        LOG.info("PolyWorks renewal skipped; Companion unavailable: %s", type(exc).__name__)


def _tray_command(*, open_window: bool = False) -> list[str]:
    command = _companion_command("account-tray") + ["--blender-pid", str(os.getpid())]
    return command + (["--open-window"] if open_window else [])


def _maybe_start_tray() -> None:
    """Keep one background Companion with its tray icon running (Windows)."""
    process = _state["tray_process"]
    if process is not None and process.poll() is None:
        return
    view = _state["view"]
    now = time.monotonic()
    if (not TRAY or getattr(bpy.app, "background", False) or view is None
            or view.companion_running or _bake_active()
            or now - _state["registered_at"] < SYNC_START_DELAY_SECONDS
            or (_state["tray_attempt"] is not None
                and now - _state["tray_attempt"] < TRAY_RETRY_SECONDS)):
        return
    _state["tray_attempt"] = now
    try:
        _state["tray_process"] = subprocess.Popen(
            _tray_command(), cwd=Path(__file__).resolve().parents[2], shell=False, **_HIDDEN)
    except (OSError, ValueError, KeyError) as exc:
        LOG.info("Cloth NeXt tray skipped; Companion unavailable: %s", type(exc).__name__)


def _tick():
    try:
        signature = file_signature(LocalStore())
        if (signature != _state["signature"]
                or time.monotonic() - _state["last_full"] >= FULL_REFRESH_SECONDS):
            _start_read(signature)
        if _state["redraw"]:
            _tag_redraw()
        _reap()
        _maybe_start_tray()
        _maybe_sync()
    except Exception as exc:
        LOG.warning("PolyWorks status timer error: %s", type(exc).__name__)
    return POLL_SECONDS


def open_companion() -> tuple[bool, str]:
    """Open the Companion account window. Local launch only; no account action."""
    root = Path(__file__).resolve().parents[2]
    if TRAY:
        # The tray Companion shows it: a running one is asked to, else this one stays as the tray.
        try:
            process = subprocess.Popen(_tray_command(open_window=True), cwd=root, shell=False,
                                       **_HIDDEN)
        except (OSError, ValueError, KeyError) as exc:
            return False, f"Cloth NeXt Companion could not be opened: {exc}"
        tray = _state["tray_process"]
        if tray is None or tray.poll() is not None:
            _state["tray_process"] = process
        return True, "Cloth NeXt Companion opened."
    view = _state["view"]
    if view is not None and view.companion_running:
        return True, "Cloth NeXt Companion is already open."
    if any(p.poll() is None for p in _state["windows"]):
        return True, "Cloth NeXt Companion is already open."
    try:
        process = subprocess.Popen(_companion_command("account"), cwd=root, shell=False, **_HIDDEN)
    except (OSError, ValueError, KeyError) as exc:
        return False, f"Cloth NeXt Companion could not be opened: {exc}"
    _state["windows"].append(process)
    return True, "Cloth NeXt Companion opened."


def register() -> None:
    for callback in _stale_timers:
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)
    _stale_timers.clear()
    _state.update(generation=_state["generation"] + 1, signature=None, view=None,
                  registered_at=time.monotonic(), last_sync=None)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)


def unregister() -> None:
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    _state["generation"] += 1  # discard any in-flight read
    worker = _state["worker"]
    if worker is not None and worker.is_alive():
        worker.join(timeout=2.0)
    _state["worker"] = None
    for process in [_state["sync_process"], _state["tray_process"], *_state["windows"]]:
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
    _state.update(sync_process=None, tray_process=None, tray_attempt=None, windows=[],
                  view=None, signature=None)


class CLOTHNEXT_OT_open_account_companion(bpy.types.Operator):
    """Opens the Companion; every account and licence action happens there."""
    bl_idname = "clothnext.open_account_companion"
    bl_label = "Open Cloth NeXt Companion"
    bl_description = ("Open Cloth NeXt Companion to manage your optional PolyWorks account "
                      "for GAIA and online services")
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        ok, message = open_companion()
        self.report({"INFO" if ok else "ERROR"}, message)
        return {"FINISHED" if ok else "CANCELLED"}


CLASSES = (CLOTHNEXT_OT_open_account_companion,)
