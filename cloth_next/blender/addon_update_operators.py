# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Add-on update operators: status from the policy-defined channel index,
package replacement exclusively inside Blender's own extension manager.

SELF-UPDATE SAFETY (the Phase-3B hotfix invariant): Cloth NeXt never
installs, replaces, disables, reloads, or unregisters its own running
extension package from its own Python stack. Invoking
``bpy.ops.extensions.package_install(pkg_id="cloth_next")`` from a Cloth
NeXt operator makes Blender disable/replace/re-enable the very extension
whose code is still executing — a native-level module-lifetime hazard that
can crash Blender and cannot be caught with try/except. Deferring the call
through ``bpy.app.timers`` does not help: the extension is still enabled
and loaded when the timer fires. The update action here is therefore a
*handoff*: synchronize the exact channel repository, then open Blender's
native extension update view where the user clicks Blender's own Update
button. A structural policy test fails the suite if any production Cloth
NeXt code calls ``package_install`` again.

Public Blender 5.1.2 API used (verified by runtime introspection; see
docs/LIMITATIONS.md for what is *not* public):

- ``bpy.ops.preferences.extension_repo_add(name=, remote_url=, type='REMOTE')``
- ``bpy.ops.extensions.repo_sync(repo_directory=)``
- ``bpy.ops.extensions.userpref_show_for_update()`` (the native update view)
- ``bpy.context.preferences.extensions.repos`` RNA
  (name/module/remote_url/enabled/directory)
- ``bpy.app.online_access``

The repository is always identified by its resolved ``directory`` (public
RNA), never by an index: the ``repo_index`` operator parameters count only
enabled repositories with valid settings, so an index into
``preferences.extensions.repos`` silently shifts as soon as any earlier
repository is disabled — in real Blender 5.1.2 that raised
"Repository not set". The directory string is copied out of the RNA before
any Blender operator runs; no RNA reference is retained across operator
calls. ``active_repo`` is UI state and is not touched.

Blender exposes no public operator or RNA to ask "does package X have an
update?", so the Check action reads the channel ``index.json`` (official
Blender-generated format, fixed project URL) in a worker thread.

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
refresh_update_session = addon_updates.run_update_check


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


def dev_access_error(context, channel: UpdateChannel) -> str:
    """Require only explicit risk acknowledgement for the public Dev channel.

    Developer Tools control internal diagnostic UI and are intentionally
    independent from a user's update-channel choice.
    """
    if channel is not UpdateChannel.DEV:
        return ""
    try:
        preferences = context.preferences.addons[_ADDON_ID].preferences
    except (KeyError, AttributeError):
        return "Dev channel preferences are unavailable."
    if not getattr(preferences, "dev_channel_acknowledged", False):
        return ("Acknowledge the Development Channel warning before checking, "
                "adding, or installing Dev updates.")
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
    """Schedule one background channel check without networking in draw()."""
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
    """Start the deferred worker after Blender has completed panel drawing."""
    global _worker, _automatic_requested_channel, _automatic_checked_channel
    channel = _automatic_requested_channel
    if channel is None:
        return None
    if _worker is not None and _worker.is_alive():
        return 0.5
    context = bpy.context
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
    _session.state = AddonUpdateState.CHECKING
    _session.message = ""

    def check() -> None:
        global _automatic_checked_channel
        addon_updates.run_update_check(_session, channel, INSTALLED_VERSION)
        _automatic_checked_channel = channel

    _worker = threading.Thread(
        target=check, daemon=True, name="clothnext-auto-update-check")
    _worker.start()
    _automatic_requested_channel = None
    if not bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.register(_ui_refresh_pulse, first_interval=0.25)
    return None


def _blender_repo_sync(directory: str) -> None:
    """Synchronize exactly one repository, identified by its directory."""
    bpy.ops.extensions.repo_sync(repo_directory=directory)


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
        repos = context.preferences.extensions.repos
        index = addon_updates.find_channel_repo(repos, channel)
        if index is None:
            _session.state = AddonUpdateState.REPOSITORY_NOT_CONFIGURED
            _session.latest = None
            _session.message = (f"The {channel.label} repository is not "
                                "configured in Blender.")
            self.report({"WARNING"}, _session.message)
            return {"FINISHED"}
        if not getattr(repos[index], "enabled", False):
            _session.state = AddonUpdateState.REPOSITORY_DISABLED
            _session.latest = None
            _session.message = (f"The {channel.label} repository is disabled "
                                "in Blender; enable it under Preferences > "
                                "Get Extensions > Repositories.")
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
        if error := dev_access_error(context, channel):
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
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


class CLOTHNEXT_OT_addon_update_through_blender(bpy.types.Operator):
    """Synchronizes the selected Cloth NeXt repository and opens Blender's native extension update view. Cloth NeXt never replaces its own files while running"""

    bl_idname = "clothnext.addon_update_through_blender"
    bl_label = "Update through Blender"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return (_session.state is AddonUpdateState.UPDATE_AVAILABLE
                and _session.latest is not None
                and _session.latest > INSTALLED_VERSION)

    def execute(self, context):
        if not self.poll(context):
            _session.state = AddonUpdateState.UP_TO_DATE
            _session.message = ("No version newer than the installed "
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
        companion_manager.shutdown()
        channel = selected_channel(context)
        if error := dev_access_error(context, channel):
            _session.state = AddonUpdateState.INSTALL_BLOCKED
            _session.message = error
            self.report({"WARNING"}, error)
            return {"CANCELLED"}
        # 6-7: the exact selected channel repository, never a substitute.
        repos = context.preferences.extensions.repos
        index = addon_updates.find_channel_repo(repos, channel)
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
        # equal, older, invalid, or ambiguous candidate before native handoff.
        refresh_update_session(_session, channel, INSTALLED_VERSION)
        if (_session.state is not AddonUpdateState.UPDATE_AVAILABLE
                or _session.latest is None
                or _session.latest <= INSTALLED_VERSION):
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


def shutdown(join_timeout: float = 5.0) -> None:
    """Join the check worker, drop the timer, reset the session state.

    Called on unregister; safe to call multiple times; leaves no thread,
    timer, or stale callback behind.
    """
    global _worker, _automatic_requested_channel, _automatic_checked_channel
    worker = _worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=join_timeout)
    _worker = None
    if bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.unregister(_ui_refresh_pulse)
    if bpy.app.timers.is_registered(_automatic_update_check_timer):
        bpy.app.timers.unregister(_automatic_update_check_timer)
    _automatic_requested_channel = None
    _automatic_checked_channel = None
    _session.reset()
    _owned_managers.clear()


CLASSES = (
    CLOTHNEXT_OT_addon_update_check,
    CLOTHNEXT_OT_addon_update_repo_setup,
    CLOTHNEXT_OT_addon_update_through_blender,
    CLOTHNEXT_OT_addon_open_extensions,
    CLOTHNEXT_OT_addon_open_release_notes,
)
