# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reuse the installed extension's owning repository for all release channels.

RNA remote_url is mutable; directory/module and running files are never changed.
Blender synchronizes that directory; its cached index supplies the decision.
The native update view completes installation outside this add-on's stack.
Never invoke package_install or any self-replacement from production code.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path

import bpy

from .. import manifest_version
from ..core.state import ApplicationState
from ..ppf.models import ConnectionOwnership
from ..updater import addon_updates
from ..updater.addon_update_guard import (ADDON_UPDATE_PREPARATION,
                                          can_start_addon_update)
from ..updater.addon_updates import AddonUpdateState, UpdateChannel
from ..updater.addon_versions import AddonVersion, parse_version
from .addon_identity import addon_preferences, package_addon_id

_ADDON_ID = package_addon_id(__package__)

# Read once at import (a local file read, no network); the manifest is the
# canonical version source (docs/RELEASE_POLICY.md section 2).
INSTALLED_VERSION: AddonVersion = parse_version(manifest_version())
DEFAULT_CHANNEL: UpdateChannel = addon_updates.default_channel(INSTALLED_VERSION)

_session = addon_updates.AddonUpdateSession()
_worker: threading.Thread | None = None
_automatic_requested_channel: UpdateChannel | None = None
_automatic_checked_channel: UpdateChannel | None = None

# Phase-3 hook: solver process managers Cloth NeXt started itself. External
# servers are never registered here and therefore never stopped.
_owned_managers: list = []


def session() -> addon_updates.AddonUpdateSession:
    return _session


def register_owned_process_manager(manager, ownership: ConnectionOwnership) -> None:
    if ownership is ConnectionOwnership.OWNED_PROCESS:
        _owned_managers.append(manager)


def _default_application_state() -> ApplicationState:
    """No live solve pipeline exists yet (Phase 3); an update is unsafe while
    an owned solver process is still running."""
    for manager in _owned_managers:
        try:
            poll = manager.poll()
        except Exception:  # noqa: BLE001 — an unpollable process is not "safe"
            return ApplicationState.STARTING
        if getattr(poll, "running", False):
            return ApplicationState.READY
    return ApplicationState.STOPPED


# Replaceable provider so the future session state machine can plug in.
application_state_provider = _default_application_state
def refresh_update_session(session, channel, installed):
    directory = prepare_repository(bpy.context, channel)
    payload = json.loads((Path(directory) / ".blender_ext" / "index.json").read_text(encoding="utf-8"))
    addon_updates.run_update_check(session, channel, installed, fetch=lambda _: payload)
    if session.state not in {AddonUpdateState.ERROR, AddonUpdateState.UNAVAILABLE}:
        session.migrated_channel = channel


def owning_repo_index(context):
    return addon_updates.find_owning_repo(
        context.preferences.extensions.repos, _ADDON_ID,
        Path(__file__).resolve().parents[1])


def prepare_repository(context, channel):
    return addon_updates.configure_owning_repo(
        context.preferences.extensions.repos, _ADDON_ID, channel,
        Path(__file__).resolve().parents[1])


def channel_changed(_preferences, context):
    global _session, _automatic_checked_channel, _automatic_requested_channel
    # Workers retain their old session, so a stale response cannot overwrite
    # the newly selected channel's state.
    _session = addon_updates.AddonUpdateSession()
    _automatic_checked_channel = None
    _automatic_requested_channel = None
    request_automatic_update_check(context)


def initialize_updates():
    request_automatic_update_check(bpy.context)


def synchronize_selected(context, channel):
    _session.migrated_channel = None
    _session.decision = None
    try:
        directory = prepare_repository(context, channel)
        _blender_repo_sync(directory)
        refresh_update_session(_session, channel, INSTALLED_VERSION)
        return _session.state not in {AddonUpdateState.ERROR, AddonUpdateState.UNAVAILABLE}
    except Exception as exc:
        _session.state = (AddonUpdateState.REPOSITORY_NOT_CONFIGURED
                          if owning_repo_index(context) is None else AddonUpdateState.SYNC_FAILED)
        index = owning_repo_index(context)
        if index is not None and not context.preferences.extensions.repos[index].enabled:
            _session.state = AddonUpdateState.REPOSITORY_DISABLED
        _session.latest = None
        _session.message = f"{exc} Installed files are unchanged; retry Check for Updates."
        return False



def _shutdown_owned_solvers() -> bool:
    """Stop only owned solver processes and confirm they exited."""
    all_exited = True
    for manager in list(_owned_managers):
        try:
            manager.stop()
            poll = manager.poll()
            if getattr(poll, "running", False):
                all_exited = False
        except Exception:  # noqa: BLE001 — treat as not-exited, never ignore
            all_exited = False
    return all_exited


def selected_channel(context) -> UpdateChannel:
    try:
        preferences = addon_preferences(context, __package__)
    except (KeyError, AttributeError):
        return DEFAULT_CHANNEL
    name = getattr(preferences, "update_channel", None)
    if name in UpdateChannel.__members__:
        return UpdateChannel[name]
    return DEFAULT_CHANNEL


def dev_access_error(context, channel: UpdateChannel) -> str:
    """Require only explicit risk acknowledgement for the public Dev channel.

    Developer Tools control internal diagnostic UI and are intentionally
    independent from a user's update-channel choice.
    """
    if channel is not UpdateChannel.DEV or INSTALLED_VERSION.channel_name == "dev":
        return ""
    try:
        preferences = addon_preferences(context, __package__)
    except (KeyError, AttributeError):
        return "Dev channel preferences are unavailable."
    if not getattr(preferences, "dev_channel_acknowledged", False):
        return ("Acknowledge the Development Channel warning before checking, "
                "synchronizing, or installing Dev updates.")
    return ""


def _tag_redraw_preferences() -> None:
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"PREFERENCES", "PROPERTIES"}:
                area.tag_redraw()


def _ui_refresh_pulse() -> float | None:
    """Timer callback: redraw preferences while the check worker runs."""
    worker = _worker
    _tag_redraw_preferences()
    if worker is None or not worker.is_alive():
        return None
    return 0.25


def _online_access_enabled() -> bool:
    return bool(getattr(bpy.app, "online_access", True))


def request_automatic_update_check(context) -> None:
    """Schedule one deferred repository sync without networking in draw()."""
    global _automatic_requested_channel
    channel = selected_channel(context)
    if (_automatic_checked_channel is channel
            or _automatic_requested_channel is channel):
        return
    _automatic_requested_channel = channel
    if not bpy.app.timers.is_registered(_automatic_update_check_timer):
        bpy.app.timers.register(_automatic_update_check_timer,
                                first_interval=0.25)


def _automatic_update_check_timer() -> float | None:
    """Sync through Blender on its main thread after panel drawing completes."""
    global _worker, _automatic_requested_channel, _automatic_checked_channel
    channel = _automatic_requested_channel
    if channel is None:
        return None
    if _worker is not None and _worker.is_alive():
        return 0.5
    context = bpy.context
    channel = selected_channel(context)
    if not _online_access_enabled():
        _session.state = AddonUpdateState.ONLINE_ACCESS_DISABLED
        _session.latest = None
        _session.message = "Enable Allow Online Access to check for updates."
        _automatic_checked_channel = channel
        _automatic_requested_channel = None
        return None
    if error := dev_access_error(context, channel):
        _session.state = AddonUpdateState.INSTALL_BLOCKED
        _session.latest = None
        _session.message = error
        _automatic_checked_channel = channel
        _automatic_requested_channel = None
        return None
    synchronize_selected(context, channel)
    _automatic_checked_channel = channel
    _automatic_requested_channel = None
    _tag_redraw_preferences()
    return None


def _blender_repo_sync(directory: str) -> None:
    """Synchronize exactly one repository, identified by its directory."""
    result = bpy.ops.extensions.repo_sync(repo_directory=directory)
    if result != {"FINISHED"}:
        raise RuntimeError(f"Repository synchronization did not finish: {result}")


def _blender_show_update_view() -> None:
    """Open Blender's native extension update view (never installs)."""
    bpy.ops.extensions.userpref_show_for_update()


class CLOTHNEXT_OT_addon_update_check(bpy.types.Operator):
    """Check the selected Cloth NeXt channel repository for an update"""

    bl_idname = "clothnext.addon_update_check"
    bl_label = "Check for Updates"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        global _worker
        if _worker is not None and _worker.is_alive():
            self.report({"INFO"}, "An update check is already running.")
            return {"CANCELLED"}
        if not _online_access_enabled():
            _session.state = AddonUpdateState.ONLINE_ACCESS_DISABLED
            _session.message = ("Enable 'Allow Online Access' in "
                                "Preferences > System to check for updates.")
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        channel = selected_channel(context)
        if error := dev_access_error(context, channel):
            _session.state = AddonUpdateState.INSTALL_BLOCKED
            _session.message = error
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
        if not synchronize_selected(context, channel):
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        return {"FINISHED"}


class CLOTHNEXT_OT_addon_update_repo_setup(bpy.types.Operator):
    """Retry configuration and sync of the installation's owning repository"""

    bl_idname = "clothnext.addon_update_repo_setup"
    bl_label = "Retry Repository Migration"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        channel = selected_channel(context)
        if error := dev_access_error(context, channel):
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
        if synchronize_selected(context, channel):
            return {"FINISHED"}
        self.report({"WARNING"}, _session.message)
        return {"CANCELLED"}


class CLOTHNEXT_OT_addon_update_through_blender(bpy.types.Operator):
    """Synchronizes the selected Cloth NeXt repository and opens Blender's native extension update view. Cloth NeXt never replaces its own files while running"""

    bl_idname = "clothnext.addon_update_through_blender"
    bl_label = "Update through Blender"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return (_session.state in addon_updates.ACTIONABLE_STATES
                and _session.latest is not None
                and addon_updates.decide_update(
                    INSTALLED_VERSION, (_session.latest,), selected_channel(context)
                ).state in addon_updates.ACTIONABLE_STATES)

    def execute(self, context):
        if not self.poll(context):
            _session.state = AddonUpdateState.UP_TO_DATE
            _session.message = ("No actionable target for the installed "
                                f"{INSTALLED_VERSION} is available; Blender's "
                                "update view was not opened.")
            self.report({"INFO"}, _session.message)
            return {"CANCELLED"}
        # 1-3: an update must be available (poll), the application must be in
        # a safe state, and online access must be enabled.
        state = application_state_provider()
        if not can_start_addon_update(state):
            _session.state = AddonUpdateState.INSTALL_BLOCKED
            _session.message = (f"Update blocked: application state is "
                                f"{state.name}. {ADDON_UPDATE_PREPARATION[0]} "
                                f"{ADDON_UPDATE_PREPARATION[1]}")
            self.report({"ERROR"}, _session.message)
            return {"CANCELLED"}
        if not _online_access_enabled():
            _session.state = AddonUpdateState.ONLINE_ACCESS_DISABLED
            _session.message = ("Enable 'Allow Online Access' in "
                                "Preferences > System to update.")
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        # 4-5: stop only Cloth NeXt-owned processes; never external servers.
        # Caches, PC2 modifiers, and scene data are never touched.
        if not _shutdown_owned_solvers():
            _session.state = AddonUpdateState.INSTALL_BLOCKED
            _session.message = ("The Cloth NeXt solver process did not exit; "
                                "stop it before updating.")
            self.report({"ERROR"}, _session.message)
            return {"CANCELLED"}
        # Quiesce UI preview jobs and close the owned Bake companion.
        # Companion ownership is deliberately separate from solver ownership.
        from . import bake_preview, companion_manager
        bake_preview.stop()
        if not companion_manager.shutdown():
            _session.state = AddonUpdateState.INSTALL_BLOCKED
            _session.message = (
                "The Cloth NeXt Bake window did not close cleanly; "
                "close it before updating.")
            self.report({"ERROR"}, _session.message)
            return {"CANCELLED"}
        channel = selected_channel(context)
        if error := dev_access_error(context, channel):
            _session.state = AddonUpdateState.INSTALL_BLOCKED
            _session.message = error
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
        # 6-7: resolve installation ownership independently of the feed URL.
        repos = context.preferences.extensions.repos
        index = owning_repo_index(context)
        if index is None:
            _session.state = AddonUpdateState.REPOSITORY_NOT_CONFIGURED
            _session.message = (f"The {channel.label} repository is not "
                                "configured in Blender.")
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        if not getattr(repos[index], "enabled", False):
            _session.state = AddonUpdateState.REPOSITORY_DISABLED
            _session.message = (f"The {channel.label} repository is disabled "
                                "in Blender; enable it under Preferences > "
                                "Get Extensions > Repositories.")
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        # Copy the directory string out of the RNA now; repository RNA may be
        # mutated by the Blender operators below and is not referenced again.
        directory = str(getattr(repos[index], "directory", "") or "")
        if not directory:
            _session.state = AddonUpdateState.REPOSITORY_NOT_CONFIGURED
            _session.message = (f"The {channel.label} repository has no valid "
                                "local directory; check its settings under "
                                "Preferences > Get Extensions > Repositories.")
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        # 8: synchronize exactly this repository through Blender.
        try:
            directory = prepare_repository(context, channel)
            _blender_repo_sync(directory)
        except Exception as exc:  # noqa: BLE001 — a distinct, honest state
            _session.state = AddonUpdateState.SYNC_FAILED
            _session.message = (f"Blender could not synchronize the "
                                f"{channel.label} repository ({exc}). Check "
                                "the network connection and try again. The "
                                "installed version is unchanged.")
            self.report({"ERROR"}, _session.message)
            return {"CANCELLED"}
        # The repository may have changed since the asynchronous status check.
        # Re-read its authoritative index after sync and refuse a stale,
        # equal, invalid, or ambiguous candidate before native handoff.
        try:
            refresh_update_session(_session, channel, INSTALLED_VERSION)
        except Exception as exc:
            _session.state = AddonUpdateState.ERROR
            _session.message = str(exc)
        if not self.poll(context):
            if _session.state is AddonUpdateState.UP_TO_DATE:
                _session.message = ("Repository synchronized; it contains no "
                                    "version newer than the installed "
                                    f"{INSTALLED_VERSION}. Blender's update "
                                    "view was not opened.")
            self.report({"ERROR"} if _session.state is AddonUpdateState.ERROR
                        else {"INFO"}, _session.message)
            return {"CANCELLED"}
        # 9-11: open Blender's native update view and hand off. Installation,
        # package replacement, and any disable/re-enable of Cloth NeXt happen
        # exclusively inside Blender's own extension manager — never here.
        try:
            _blender_show_update_view()
        except Exception as exc:  # noqa: BLE001 — manual path, no self-install
            _session.state = AddonUpdateState.READY_IN_BLENDER
            _session.message = ("Repository synchronized, but the update view "
                                f"could not be opened here ({exc}). Open "
                                "Edit > Preferences > Get Extensions and "
                                "click Update on Cloth NeXt. Channel URL: "
                                f"{channel.index_url}")
            self.report({"WARNING"}, _session.message)
            return {"FINISHED"}
        _session.state = AddonUpdateState.READY_IN_BLENDER
        _session.message = ("Repository synchronized. Click Update on Cloth "
                            "NeXt in Blender's Get Extensions view, then "
                            "restart Blender when prompted.")
        self.report({"INFO"}, _session.message)
        return {"FINISHED"}


class CLOTHNEXT_OT_addon_open_extensions(bpy.types.Operator):
    """Open Blender's Get Extensions preferences (manual update path)"""

    bl_idname = "clothnext.addon_open_extensions"
    bl_label = "Open Blender Extensions"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        try:
            bpy.ops.extensions.userpref_show_for_update()
        except Exception:  # noqa: BLE001 — tell the user the manual path
            channel = selected_channel(context)
            self.report({"WARNING"},
                        "Open Edit > Preferences > Get Extensions manually; "
                        f"channel URL: {channel.index_url}")
            return {"CANCELLED"}
        return {"FINISHED"}


class CLOTHNEXT_OT_addon_open_release_notes(bpy.types.Operator):
    """Open the Cloth NeXt release notes (documentation only, not an update source)"""

    bl_idname = "clothnext.addon_open_release_notes"
    bl_label = "Open Release Notes"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        webbrowser.open(addon_updates.release_notes_url(_session.latest))
        return {"FINISHED"}


def shutdown(join_timeout: float = 5.0) -> bool:
    """Join the check worker without forgetting one that exceeds the timeout."""
    global _worker, _automatic_requested_channel, _automatic_checked_channel
    worker = _worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=max(0.0, float(join_timeout)))
    stopped = worker is None or not worker.is_alive()
    if stopped and _worker is worker:
        _worker = None
    if bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.unregister(_ui_refresh_pulse)
    if bpy.app.timers.is_registered(_automatic_update_check_timer):
        bpy.app.timers.unregister(_automatic_update_check_timer)
    _automatic_requested_channel = None
    _automatic_checked_channel = None
    if stopped:
        _session.reset()
    _owned_managers.clear()
    return stopped


CLASSES = (
    CLOTHNEXT_OT_addon_update_check,
    CLOTHNEXT_OT_addon_update_repo_setup,
    CLOTHNEXT_OT_addon_update_through_blender,
    CLOTHNEXT_OT_addon_open_extensions,
    CLOTHNEXT_OT_addon_open_release_notes,
)
