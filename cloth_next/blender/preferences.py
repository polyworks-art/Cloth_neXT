# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Add-on preferences: the "PPF Contact Solver" section and its operators.

The panel only renders what the pure ``updater.view_model`` module computes.
Downloads never start automatically — not on enable, file open, simulation
start, Blender start, or in the background. Every download begins with the
explicit confirmation dialog of ``CLOTHNEXT_OT_solver_download``. Blocking
work runs in a worker thread; only this module touches ``bpy``.
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

import bpy

from ..ppf.compatibility import parse_executable_version
from ..updater import addon_updates, view_model
from . import addon_update_operators
from ..updater.install_paths import ManagedSolverPaths, read_current
from ..updater.managed import ManagedSolverInstaller
from ..updater.modes import InstallationMode
from ..updater.solver_manifest import (SolverCompatibilityEntry,
                                                load_bundled_manifest)
from ..updater.states import InstallerAction, InstallerState

_ADDON_ID = __package__.partition(".blender")[0]
_PLATFORM = "windows-x86_64"


class _SolverSession:
    """Session-scoped installer state; never populated at import time."""

    def __init__(self) -> None:
        self.entry: SolverCompatibilityEntry | None = None
        self.disabled_reason: str | None = None
        self.installer: ManagedSolverInstaller | None = None
        self.worker: threading.Thread | None = None
        self.loaded = False

    def load(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        try:
            manifest = load_bundled_manifest()
            self.entry = manifest.entry_for(_PLATFORM)
            if self.entry is None:
                self.disabled_reason = f"no verified release for {_PLATFORM}"
        except (OSError, ValueError) as exc:
            self.entry = None
            self.disabled_reason = str(exc)

    def ensure_installer(self) -> ManagedSolverInstaller | None:
        self.load()
        if self.entry is None:
            return None
        if self.installer is None:
            extension_root = Path(__file__).resolve().parents[1]
            self.installer = ManagedSolverInstaller(
                ManagedSolverPaths.default(), self.entry,
                probe_version=_probe_version, health_check=_health_check,
                forbidden_roots=(extension_root,))
        return self.installer


_session = _SolverSession()


def _probe_version(executable: Path) -> tuple[str, str, str]:
    import subprocess
    completed = subprocess.run([str(executable), "--version"], capture_output=True,
                               text=True, timeout=60, check=True, shell=False)
    return parse_executable_version(completed.stdout + completed.stderr)


def _health_check(executable: Path) -> bool:
    """Real health check: start the server, verify readiness plus status, stop it."""
    from ..updater.health_runner import run_real_health_check
    return run_real_health_check(executable)


def _safe_read_current():
    """Tampered current.json is treated as a repair case, never trusted."""
    try:
        return read_current(ManagedSolverPaths.default()), True
    except ValueError:
        return None, False


def _installer_state() -> InstallerState:
    installer = _session.installer
    if installer is not None:
        return installer.state
    active, valid = _safe_read_current()
    if not valid:
        return InstallerState.REPAIR_REQUIRED
    if active is not None:
        return InstallerState.READY
    return InstallerState.NOT_INSTALLED


def _installed_info() -> view_model.InstalledInfo | None:
    _session.load()
    active, _valid = _safe_read_current()
    if active is None or _session.entry is None:
        return None
    return view_model.InstalledInfo(
        InstallationMode.MANAGED_INSTALLATION,
        active.version, _session.entry.protocol_version,
        _session.entry.schema_version)


def _tag_redraw_preferences() -> None:
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "PREFERENCES":
                area.tag_redraw()


def _ui_refresh_pulse() -> float | None:
    """Timer callback: redraw preferences while the installer worker runs."""
    worker = _session.worker
    _tag_redraw_preferences()
    if worker is None or not worker.is_alive():
        return None  # worker finished; final redraw done, stop the timer
    return 0.25


def _run_in_worker(target) -> None:
    if _session.worker is not None and _session.worker.is_alive():
        return
    _session.worker = threading.Thread(target=target, daemon=True,
                                       name="clothnext-solver-installer")
    _session.worker.start()
    if not bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.register(_ui_refresh_pulse, first_interval=0.25)


def shutdown(join_timeout: float = 10.0) -> None:
    """Cancel running downloads and join the worker; called on unregister.

    Leaves no running worker thread, no open download handle, no UI refresh
    timer, and no partially started installer behind. Safe to call multiple
    times.
    """
    installer = _session.installer
    if installer is not None:
        installer.cancel()
    worker = _session.worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=join_timeout)
    if bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.unregister(_ui_refresh_pulse)
    _session.worker = None
    _session.installer = None
    _session.entry = None
    _session.disabled_reason = None
    _session.loaded = False


class _SolverInstallDialog:
    """Shared confirmation-dialog behavior for download and repair.

    Deliberately a plain mixin, NOT a registered Operator subclass:
    registering a subclass of an already registered Operator corrupts
    Blender's RNA↔Python class mapping, after which the parent operator's
    ``invoke`` is silently skipped and its button appears to do nothing.
    """

    def _report_online_access_blocked(self) -> bool:
        if getattr(bpy.app, "online_access", True):
            return False
        self.report({"ERROR"}, "Blender's online access is disabled. Enable "
                    "'Allow Online Access' in Preferences > System to download "
                    "the solver.")
        return True

    def draw(self, _context):
        layout = self.layout
        entry = _session.entry
        if entry is None:
            return
        for line in view_model.confirmation_lines(entry, ManagedSolverPaths.default()):
            layout.label(text=line)
        row = layout.row()
        row.operator("clothnext.solver_open_download_page", text="View Official Source")
        row.operator("clothnext.solver_view_licenses", text="View License Information")
        layout.label(text="Click OK to download and install; press Esc to cancel.")

    def execute(self, _context):
        installer = _session.ensure_installer()
        if installer is None:
            return {"CANCELLED"}
        if installer.state is not InstallerState.AWAITING_CONFIRMATION:
            # Never crash the worker with an invalid transition; tell the user.
            self.report({"WARNING"}, "The download was not confirmed; "
                        "nothing was started.")
            return {"CANCELLED"}
        _run_in_worker(lambda: installer.install(confirmed=True))
        self.report({"INFO"}, "Downloading the official solver in the background.")
        return {"FINISHED"}

    def cancel(self, _context):
        installer = _session.installer
        if installer is not None and installer.state is InstallerState.AWAITING_CONFIRMATION:
            installer.install(confirmed=False)


class CLOTHNEXT_OT_solver_download(_SolverInstallDialog, bpy.types.Operator):
    """Download the official PPF Contact Solver after explicit confirmation"""
    bl_idname = "clothnext.solver_download"
    bl_label = "Download Official Solver"
    bl_options = {"REGISTER", "INTERNAL"}

    def invoke(self, context, _event):
        if self._report_online_access_blocked():
            return {"CANCELLED"}
        installer = _session.ensure_installer()
        if installer is None:
            self.report({"ERROR"}, "Automatic download is disabled: "
                        f"{_session.disabled_reason}")
            return {"CANCELLED"}
        if _session.worker is not None and _session.worker.is_alive():
            self.report({"INFO"}, "A solver installation is already running.")
            return {"CANCELLED"}
        installer.request_download()
        return context.window_manager.invoke_props_dialog(self, width=520)


class CLOTHNEXT_OT_solver_cancel(bpy.types.Operator):
    """Cancel the running solver download"""
    bl_idname = "clothnext.solver_cancel"
    bl_label = "Cancel Download"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        if _session.installer is not None:
            _session.installer.cancel()
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_select_existing(bpy.types.Operator):
    """Select an existing external solver installation (never modified)"""
    bl_idname = "clothnext.solver_select_existing"
    bl_label = "Select Existing Installation"
    bl_options = {"INTERNAL"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, _context):
        from ..updater.external import validate_external_installation
        _session.load()
        if _session.entry is None:
            self.report({"ERROR"}, "No compatibility manifest entry is available.")
            return {"CANCELLED"}
        try:
            result = validate_external_installation(Path(self.filepath),
                                                    _probe_version, _session.entry)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, f"Invalid installation: {exc}")
            return {"CANCELLED"}
        if not result.compatible:
            self.report({"WARNING"},
                        f"Installed protocol {result.protocol_version} is not "
                        f"compatible; required {_session.entry.protocol_version}.")
            return {"CANCELLED"}
        preferences = bpy.context.preferences.addons[_ADDON_ID].preferences
        preferences.external_solver_path = str(result.root)
        self.report({"INFO"}, f"External solver {result.package_version} selected.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_open_download_page(bpy.types.Operator):
    """Open the official st-tech/ppf-contact-solver release page"""
    bl_idname = "clothnext.solver_open_download_page"
    bl_label = "Open Official Download Page"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        _session.load()
        url = (_session.entry.official_release_page if _session.entry
               else "https://github.com/st-tech/ppf-contact-solver/releases")
        webbrowser.open(url)
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_view_licenses(bpy.types.Operator):
    """Open the upstream license information"""
    bl_idname = "clothnext.solver_view_licenses"
    bl_label = "View License Information"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        webbrowser.open("https://github.com/st-tech/ppf-contact-solver/blob/main/LICENSE")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_health_check(bpy.types.Operator):
    """Run the real health check against the active installation"""
    bl_idname = "clothnext.solver_health_check"
    bl_label = "Run Health Check"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        paths = ManagedSolverPaths.default()
        active, valid = _safe_read_current()
        if not valid:
            self.report({"ERROR"}, "The installation metadata is damaged; "
                        "repair the managed installation.")
            return {"CANCELLED"}
        if active is None:
            self.report({"ERROR"}, "No managed solver installation is active.")
            return {"CANCELLED"}
        try:
            executable = active.executable_path(paths)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _run_in_worker(lambda: _health_check(executable))
        self.report({"INFO"}, "Health check started in the background.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_check_update(bpy.types.Operator):
    """Check whether a manifest-verified compatible update exists"""
    bl_idname = "clothnext.solver_check_update"
    bl_label = "Check for Compatible Update"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        installer = _session.ensure_installer()
        if installer is None:
            self.report({"ERROR"}, "No compatibility manifest entry is available.")
            return {"CANCELLED"}
        state = installer.check_for_update()
        self.report({"INFO"},
                    "A compatible update is available."
                    if state is InstallerState.UPDATE_AVAILABLE
                    else "The installed version matches the compatibility manifest.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_repair(_SolverInstallDialog, bpy.types.Operator):
    """Repair the managed installation by reinstalling the verified official release"""
    bl_idname = "clothnext.solver_repair"
    bl_label = "Repair Managed Installation"
    bl_options = {"REGISTER", "INTERNAL"}

    def invoke(self, context, _event):
        if self._report_online_access_blocked():
            return {"CANCELLED"}
        installer = _session.ensure_installer()
        if installer is None:
            self.report({"ERROR"}, "Automatic download is disabled: "
                        f"{_session.disabled_reason}")
            return {"CANCELLED"}
        try:
            installer.prepare_repair()
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=520)


class CLOTHNEXT_OT_solver_remove_managed(bpy.types.Operator):
    """Remove the managed solver installation (external installs are never touched)"""
    bl_idname = "clothnext.solver_remove_managed"
    bl_label = "Remove Managed Installation"
    bl_options = {"INTERNAL"}

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, _context):
        installer = _session.ensure_installer()
        active, _valid = _safe_read_current()
        if installer is None or active is None:
            self.report({"ERROR"}, "No managed installation to remove.")
            return {"CANCELLED"}
        try:
            installer.remove(active.version)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Managed installation removed.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_open_folder(bpy.types.Operator):
    """Open the managed solver installation folder"""
    bl_idname = "clothnext.solver_open_folder"
    bl_label = "Open Installation Folder"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        import os
        paths = ManagedSolverPaths.default()
        if not paths.root.is_dir():
            self.report({"ERROR"}, "The managed solver folder does not exist yet.")
            return {"CANCELLED"}
        os.startfile(paths.root)  # noqa: S606 — user-requested folder open
        return {"FINISHED"}


_ACTION_OPERATORS = {
    InstallerAction.DOWNLOAD_OFFICIAL_SOLVER: "clothnext.solver_download",
    InstallerAction.SELECT_EXISTING_INSTALLATION: "clothnext.solver_select_existing",
    InstallerAction.OPEN_OFFICIAL_DOWNLOAD_PAGE: "clothnext.solver_open_download_page",
    InstallerAction.CANCEL: "clothnext.solver_cancel",
    InstallerAction.RUN_HEALTH_CHECK: "clothnext.solver_health_check",
    InstallerAction.CHECK_FOR_COMPATIBLE_UPDATE: "clothnext.solver_check_update",
    InstallerAction.INSTALL_COMPATIBLE_VERSION: "clothnext.solver_download",
    InstallerAction.REPAIR_MANAGED_INSTALLATION: "clothnext.solver_repair",
    InstallerAction.REMOVE_MANAGED_INSTALLATION: "clothnext.solver_remove_managed",
    InstallerAction.OPEN_INSTALLATION_FOLDER: "clothnext.solver_open_folder",
    InstallerAction.SELECT_ANOTHER_INSTALLATION: "clothnext.solver_select_existing",
    InstallerAction.RETRY: "clothnext.solver_download",
}


class CLOTHNEXT_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = _ADDON_ID

    external_solver_path: bpy.props.StringProperty(
        name="External Solver Path", subtype="DIR_PATH", default="",
        description="Existing PPF Contact Solver installation selected by you; "
                    "Cloth NeXt never modifies it")

    update_channel: bpy.props.EnumProperty(
        name="Update Channel",
        items=(("STABLE", "Stable", "Official stable releases only"),
               ("BETA", "Beta", "Beta and release-candidate prereleases"),
               ("DEV", "Dev", "Unsupported public experimental snapshots")),
        default=addon_update_operators.DEFAULT_CHANNEL.name,
        description="Which Cloth NeXt release channel to check for add-on "
                    "updates (independent of the PPF solver)")
    dev_channel_acknowledged: bpy.props.BoolProperty(
        name="I understand the Dev channel risks", default=False)

    developer_tools: bpy.props.BoolProperty(
        name="Developer Test Tools", default=False,
        description="Show the Phase-3A developer actions (Create PPF Test "
                    "Scene, Run Real Solver Test) in the Cache panel")
    auto_launch_bake_window: bpy.props.BoolProperty(name="Open Bake Window Automatically", default=True)
    show_bake_hud: bpy.props.BoolProperty(name="Show Bake HUD", default=True)
    bake_hud_mode: bpy.props.EnumProperty(name="HUD Mode", items=(("COMPACT", "Compact", "Compact status card"),("EXPANDED", "Expanded", "Detailed status and telemetry")), default="EXPANDED")
    bake_hud_anchor: bpy.props.EnumProperty(name="HUD Anchor", items=(("TOP_LEFT", "Top Left", ""),("TOP_RIGHT", "Top Right", ""),("BOTTOM_LEFT", "Bottom Left", ""),("BOTTOM_RIGHT", "Bottom Right", "")), default="BOTTOM_LEFT")
    bake_hud_scale: bpy.props.FloatProperty(name="HUD Scale", default=1.0, min=0.75, max=2.0)
    show_hardware_metrics: bpy.props.BoolProperty(name="Hardware Metrics", default=True)
    telemetry_refresh_seconds: bpy.props.FloatProperty(name="Telemetry Refresh", default=1.0, min=0.25, max=10.0, subtype="TIME")

    def draw(self, _context) -> None:
        layout = self.layout
        self._draw_addon_update_section(layout)
        self._draw_solver_section(layout)
        layout.prop(self, "developer_tools")
        layout.prop(self, "auto_launch_bake_window")
        hud_box=layout.box(); hud_box.label(text="Bake HUD")
        for name in ("show_bake_hud","bake_hud_mode","bake_hud_anchor","bake_hud_scale","show_hardware_metrics","telemetry_refresh_seconds"): hud_box.prop(self,name)

    def _draw_addon_update_section(self, layout) -> None:
        """Cloth NeXt's own update status; never performs network work."""
        box = layout.box()
        box.label(text="Cloth NeXt")
        update_session = addon_update_operators.session()
        view = addon_updates.build_section_view(update_session.state,
                                                update_session.latest,
                                                update_session.message)
        box.label(text="Installed Version: "
                       f"{addon_update_operators.INSTALLED_VERSION}")
        box.prop(self, "update_channel")
        if self.update_channel == "DEV":
            warning=box.box(); warning.label(text="Development Channel", icon="ERROR")
            warning.label(text="Experimental public builds; reduced validation.")
            warning.label(text="Back up your files before updating.")
            warning.label(text=addon_updates.UpdateChannel.DEV.index_url)
            warning.prop(self,"dev_channel_acknowledged")
        box.label(text=f"Update Status: {view.status_text}")
        if view.message:
            box.label(text=view.message)
        actions = box.column()
        check = actions.row()
        check.enabled = view.check_enabled
        check.operator("clothnext.addon_update_check")
        if view.show_repo_setup:
            actions.operator("clothnext.addon_update_repo_setup")
        if view.show_install:
            actions.operator("clothnext.addon_update_install")
        elif view.show_open_extensions:
            actions.operator("clothnext.addon_open_extensions")
        actions.operator("clothnext.addon_open_release_notes")

    def _draw_solver_section(self, layout) -> None:
        box = layout.box()
        box.label(text="PPF Contact Solver")
        _session.load()
        state = _installer_state()
        installer = _session.installer
        progress_text = None
        if installer is not None and state is InstallerState.DOWNLOADING:
            done, total = installer.download_progress
            progress_text = view_model.format_download_progress(done, total)
        section = view_model.build_section(state, _session.entry,
                                           _session.disabled_reason, _installed_info(),
                                           download_progress=progress_text)
        for label, value in section.rows:
            row = box.row()
            row.label(text=f"{label}: {value}")
        if section.message:
            box.label(text=section.message)
        if (installer is not None and installer.error is not None
                and state is InstallerState.ERROR):
            box.label(text=installer.error.user_message, icon="ERROR")
        actions = box.column()
        for action in section.actions:
            idname = _ACTION_OPERATORS.get(action)
            if idname is not None:
                actions.operator(idname)
        if self.external_solver_path:
            box.label(text=f"External installation: {self.external_solver_path}")


CLASSES = (
    CLOTHNEXT_OT_solver_download,
    CLOTHNEXT_OT_solver_cancel,
    CLOTHNEXT_OT_solver_select_existing,
    CLOTHNEXT_OT_solver_open_download_page,
    CLOTHNEXT_OT_solver_view_licenses,
    CLOTHNEXT_OT_solver_health_check,
    CLOTHNEXT_OT_solver_check_update,
    CLOTHNEXT_OT_solver_repair,
    CLOTHNEXT_OT_solver_remove_managed,
    CLOTHNEXT_OT_solver_open_folder,
    CLOTHNEXT_AddonPreferences,
)
