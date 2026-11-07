# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender side of the opt-in diagnostics upload.

Starts one background thread for the whole Blender session (register to
unregister). The user switches it in Preferences > Cloth NeXt > Diagnostics;
it is on by default (Tim, 2026-11-06), and ``CLOTH_NEXT_NO_TELEMETRY=1`` or Blender's
"Allow Online Access" switched off keep it from sending. All network access
happens on the diagnostics thread; nothing here runs on Blender's UI thread
except reading preferences.
"""
from __future__ import annotations

import json
import logging
import os

import bpy

from .addon_identity import package_addon_id

_ADDON_ID = package_addon_id(__package__)
OPT_OUT_ENV = "CLOTH_NEXT_NO_TELEMETRY"
TEXT_NAME = "Cloth NeXt Diagnostics"

_service = None
_handler = None
_loggers: list[logging.Logger] = []
_unsubscribe = None
_was_active = False
_log = logging.getLogger(__name__)


def opted_out_by_environment() -> bool:
    return os.environ.get(OPT_OUT_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _preference_allows() -> bool:
    try:
        addon = bpy.context.preferences.addons.get(_ADDON_ID)
        return bool(addon and getattr(addon.preferences, "send_diagnostics", True))
    except Exception:  # noqa: BLE001
        return False


def _environment() -> dict:
    from .. import manifest_version
    from ..diagnostics.sanitize import clean_text
    import platform
    system = " ".join(p for p in (platform.system(), platform.release()) if p)
    extra = ", ".join(p for p in (platform.version(), platform.machine()) if p)
    try:
        addon_version = manifest_version()
    except Exception:  # noqa: BLE001
        addon_version = ""
    return {"addon_version": addon_version,
            "blender_version": getattr(bpy.app, "version_string", ""),
            "operating_system": clean_text(f"{system} ({extra})" if extra else system, 128)}


def _account_token():
    """Signed-in PolyWorks session, read on the diagnostics thread only."""
    from . import gaia_account
    return gaia_account.diagnostics_session_token()


def _on_bake(snapshot) -> None:
    global _was_active
    active = bool(snapshot.active)
    if _was_active and not active and _service is not None and not snapshot.preview:
        _service.record_bake(snapshot)
    _was_active = active


def apply_consent() -> None:
    """Called from the preference update: on starts collecting, off deletes everything."""
    if _service is None:
        return
    allowed = _preference_allows() and not opted_out_by_environment()
    _service.set_consent(allowed)
    _service.set_online(bool(getattr(bpy.app, "online_access", True)))


def service():
    return _service


def register() -> None:
    global _service, _handler, _unsubscribe
    if _service is not None:
        return
    try:
        from ..bake.controller import shared_controller
        from ..diagnostics import DiagnosticsLogHandler, DiagnosticsService
        from ..diagnostics.store import SessionMarker, diagnostics_root
        from ..diagnostics.transport import api_origin
        from ..telemetry import shared_telemetry
        from ..telemetry.gpu import query_nvidia_smi
        from ..telemetry.system import WindowsSystemProvider
        from ..core.logging import LOGGER_NAME
        try:
            origin = api_origin()
        except ValueError:
            origin = None
        root = diagnostics_root()
        _service = DiagnosticsService(
            root, environment=_environment(), origin=origin,
            token_provider=_account_token,
            system_provider=WindowsSystemProvider() if os.name == "nt" else None,
            gpu_provider=query_nvidia_smi,
            telemetry_snapshot=shared_telemetry.snapshot,
            bake_active=lambda: shared_controller.snapshot().active)
        apply_consent()
        # Crash marker and crash files of the previous session: always read
        # here, before crash_trace.enable() empties its file; only kept with consent.
        _service.collect_previous_session(SessionMarker(root))
        _handler = DiagnosticsLogHandler(_service)
        for name in dict.fromkeys((LOGGER_NAME, _ADDON_ID)):
            logger = logging.getLogger(name)
            logger.addHandler(_handler)
            _loggers.append(logger)
        _unsubscribe = shared_controller.subscribe(_on_bake)
        _service.start()
    except Exception:  # noqa: BLE001 - diagnostics must never block the add-on
        _log.debug("diagnostics not started", exc_info=True)


def unregister() -> None:
    global _service, _handler, _unsubscribe
    if _unsubscribe is not None:
        _unsubscribe()
        _unsubscribe = None
    if _handler is not None:
        for logger in _loggers:
            logger.removeHandler(_handler)
        _loggers.clear()
        _handler = None
    if _service is not None:
        try:
            from ..diagnostics.store import SessionMarker
            _service.stop()
            SessionMarker(_service.root).end(_service.session_id)
        except Exception:  # noqa: BLE001
            pass
        _service = None


class CLOTHNEXT_OT_show_diagnostics(bpy.types.Operator):
    """Show exactly what the next diagnostics package would contain"""
    bl_idname = "clothnext.show_diagnostics"
    bl_label = "Show Diagnostics Data"

    def execute(self, context):
        if _service is None:
            self.report({"WARNING"}, "Diagnostics are not running")
            return {"CANCELLED"}
        text = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
        text.clear()
        text.write(json.dumps(_service.preview(), indent=2))
        self.report({"INFO"}, f"Written to the Text block '{TEXT_NAME}' (Text Editor)")
        return {"FINISHED"}


CLASSES = (CLOTHNEXT_OT_show_diagnostics,)
