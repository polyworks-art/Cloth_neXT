# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Add-on update operators: status from the policy-defined channel index,
installation exclusively through Blender's own extension mechanism.

Public Blender 5.1.2 API used (verified by runtime introspection; see
docs/LIMITATIONS.md for what is *not* public):

- ``bpy.ops.preferences.extension_repo_add(name=, remote_url=, type='REMOTE')``
- ``bpy.ops.extensions.repo_sync(repo_index=)``
- ``bpy.ops.extensions.package_upgrade_all(use_active_only=True)``
- ``bpy.ops.extensions.userpref_show_for_update()`` (fallback view)
- ``bpy.context.preferences.extensions.repos`` RNA (name/module/remote_url/enabled)
- ``bpy.app.online_access``

Blender exposes no public operator or RNA to ask "does package X have an
update?", so the Check action reads the channel ``index.json`` (official
Blender-generated format, fixed project URL) in a worker thread. Blender's
own ``repo_sync`` is invoked on the install path so the actual installation
is performed and verified by Blender, never by Cloth NeXt itself.

Never touched here: the separately installed PPF solver, its files, and its
installation metadata. Add-on updates and solver updates stay separate.
"""

from __future__ import annotations

import threading
import webbrowser

import bpy

from .. import manifest_version
from ..core.state import ApplicationState
from ..ppf.models import ConnectionOwnership
from ..updater import addon_updates
from ..updater.addon_update_guard import (ADDON_UPDATE_PREPARATION,
                                          can_start_addon_update)
from ..updater.addon_updates import AddonUpdateState, UpdateChannel
from ..updater.addon_versions import AddonVersion, parse_version

_ADDON_ID = __package__.partition(".blender")[0]

# Read once at import (a local file read, no network); the manifest is the
# canonical version source (docs/RELEASE_POLICY.md section 2).
INSTALLED_VERSION: AddonVersion = parse_version(manifest_version())
DEFAULT_CHANNEL: UpdateChannel = addon_updates.default_channel(INSTALLED_VERSION)

_session = addon_updates.AddonUpdateSession()
_worker: threading.Thread | None = None

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
        preferences = context.preferences.addons[_ADDON_ID].preferences
    except (KeyError, AttributeError):
        return DEFAULT_CHANNEL
    name = getattr(preferences, "update_channel", None)
    if name in UpdateChannel.__members__:
        return UpdateChannel[name]
    return DEFAULT_CHANNEL


def _tag_redraw_preferences() -> None:
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "PREFERENCES":
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
        repos = context.preferences.extensions.repos
        index = addon_updates.find_channel_repo(repos, channel)
        if index is None or not getattr(repos[index], "enabled", False):
            _session.state = AddonUpdateState.REPOSITORY_NOT_CONFIGURED
            _session.latest = None
            _session.message = (f"The {channel.label} repository is not "
                                "configured (or disabled) in Blender.")
            self.report({"WARNING"}, _session.message)
            return {"FINISHED"}
        _session.state = AddonUpdateState.CHECKING
        _session.message = ""
        _worker = threading.Thread(
            target=lambda: addon_updates.run_update_check(
                _session, channel, INSTALLED_VERSION),
            daemon=True, name="clothnext-addon-update-check")
        _worker.start()
        if not bpy.app.timers.is_registered(_ui_refresh_pulse):
            bpy.app.timers.register(_ui_refresh_pulse, first_interval=0.25)
        self.report({"INFO"}, f"Checking the {channel.label} channel for updates.")
        return {"FINISHED"}


class CLOTHNEXT_OT_addon_update_repo_setup(bpy.types.Operator):
    """Add the selected Cloth NeXt channel as a Blender extension repository"""

    bl_idname = "clothnext.addon_update_repo_setup"
    bl_label = "Add Channel Repository"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        channel = selected_channel(context)
        repos = context.preferences.extensions.repos
        if addon_updates.find_channel_repo(repos, channel) is not None:
            self.report({"INFO"}, f"The {channel.label} repository is already "
                        "configured; no duplicate was created.")
            return {"CANCELLED"}
        try:
            bpy.ops.preferences.extension_repo_add(
                name=f"Cloth NeXt {channel.label}",
                remote_url=channel.index_url, type="REMOTE")
        except Exception as exc:  # noqa: BLE001 — tell the user the manual path
            self.report({"ERROR"},
                        f"Could not add the repository automatically ({exc}). "
                        "Add it manually: Preferences > Get Extensions > "
                        f"Repositories > + > Add Remote Repository, URL: "
                        f"{channel.index_url}")
            return {"CANCELLED"}
        if _session.state is AddonUpdateState.REPOSITORY_NOT_CONFIGURED:
            _session.state = AddonUpdateState.NOT_CHECKED
            _session.message = ""
        self.report({"INFO"}, f"Added the Cloth NeXt {channel.label} repository. "
                    "Click 'Check for Updates' next.")
        return {"FINISHED"}


class CLOTHNEXT_OT_addon_update_install(bpy.types.Operator):
    """Install the available Cloth NeXt update through Blender's own extension system"""

    bl_idname = "clothnext.addon_update_install"
    bl_label = "Install Update"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return _session.state is AddonUpdateState.UPDATE_AVAILABLE

    def execute(self, context):
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
                                "Preferences > System to install the update.")
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        if not _shutdown_owned_solvers():
            _session.state = AddonUpdateState.INSTALL_BLOCKED
            _session.message = ("The Cloth NeXt solver process did not exit; "
                                "stop it before updating.")
            self.report({"ERROR"}, _session.message)
            return {"CANCELLED"}
        # Companion ownership is deliberately separate from solver ownership.
        from . import companion_manager
        companion_manager.shutdown()
        channel = selected_channel(context)
        repos = context.preferences.extensions.repos
        index = addon_updates.find_channel_repo(repos, channel)
        if index is None:
            _session.state = AddonUpdateState.REPOSITORY_NOT_CONFIGURED
            _session.message = (f"The {channel.label} repository is not "
                                "configured in Blender.")
            self.report({"WARNING"}, _session.message)
            return {"CANCELLED"}
        _session.state = AddonUpdateState.INSTALLING
        try:
            context.preferences.extensions.active_repo = index
            bpy.ops.extensions.repo_sync(repo_index=index)
            bpy.ops.extensions.package_upgrade_all(use_active_only=True)
        except Exception as exc:  # noqa: BLE001 — fall back to Blender's own view
            _session.state = AddonUpdateState.UNAVAILABLE
            _session.message = ("Blender's automatic update could not run "
                                f"({exc}). Blender's extension update view was "
                                "opened; click 'Update' on Cloth NeXt there.")
            try:
                bpy.ops.extensions.userpref_show_for_update()
            except Exception:  # noqa: BLE001 — the message still tells the path
                pass
            self.report({"WARNING"}, _session.message)
            return {"FINISHED"}
        _session.state = AddonUpdateState.RESTART_REQUIRED
        _session.message = ("The update was handed to Blender's extension "
                            "system. Restart Blender to complete it.")
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


def shutdown(join_timeout: float = 5.0) -> None:
    """Join the check worker, drop the timer, reset the session state.

    Called on unregister; safe to call multiple times; leaves no thread,
    timer, or stale callback behind.
    """
    global _worker
    worker = _worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=join_timeout)
    _worker = None
    if bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.unregister(_ui_refresh_pulse)
    _session.reset()
    _owned_managers.clear()


CLASSES = (
    CLOTHNEXT_OT_addon_update_check,
    CLOTHNEXT_OT_addon_update_repo_setup,
    CLOTHNEXT_OT_addon_update_install,
    CLOTHNEXT_OT_addon_open_extensions,
    CLOTHNEXT_OT_addon_open_release_notes,
)
