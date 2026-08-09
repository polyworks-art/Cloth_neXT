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
from dataclasses import replace
from pathlib import Path

import bpy

from ..developer import is_dev_build

from ..ppf.compatibility import parse_executable_version
from ..ppf.layout import BundledSolverLayout
from ..ppf.solver_overlay import apply_solver_overlay
from ..updater import addon_updates, view_model
from . import addon_update_operators, icon_registry
from .addon_identity import addon_preferences, package_addon_id
from ..updater.install_paths import ManagedSolverPaths, read_current
from ..updater.managed import ManagedSolverInstaller
from ..updater.modes import InstallationMode
from ..updater.solver_manifest import (SolverCompatibilityEntry,
                                                load_bundled_manifest)
from ..updater.states import InstallerAction, InstallerState
from ..updater.update_check import solver_update_available
from ..updater.solver_registry import (
    SolverInstallation, SolverRegistry, external_installation_id, load_registry,
    migrate_legacy_current, write_registry)

_ADDON_ID = package_addon_id(__package__)
_PLATFORM = "windows-x86_64"


class _SolverSession:
    """Session-scoped installer state; never populated at import time."""

    def __init__(self) -> None:
        self.entry: SolverCompatibilityEntry | None = None
        self.entries: tuple[SolverCompatibilityEntry, ...] = ()
        self.target_entry: SolverCompatibilityEntry | None = None
        self.activate_after_install = False
        self.reinstall = False
        self.disabled_reason: str | None = None
        self.installer: ManagedSolverInstaller | None = None
        self.worker: threading.Thread | None = None
        self.worker_status: str | None = None
        self.worker_error: str | None = None
        self.loaded = False

    def load(self) -> None:
        if self.loaded:
            return
        self.loaded = True
        try:
            manifest = load_bundled_manifest()
            self.entry = manifest.entry_for(_PLATFORM)
            self.entries = manifest.releases_for(_PLATFORM)
            if self.entry is None:
                self.disabled_reason = f"no verified release for {_PLATFORM}"
        except (OSError, ValueError) as exc:
            self.entry = None
            self.disabled_reason = str(exc)

    def ensure_installer(self, release_id: str = "") -> ManagedSolverInstaller | None:
        self.load()
        entry = next((item for item in self.entries
                      if item.release_id == release_id), self.entry)
        if entry is None:
            return None
        if self.installer is None or self.installer.entry != entry:
            extension_root = Path(__file__).resolve().parents[1]
            self.installer = ManagedSolverInstaller(
                ManagedSolverPaths.default(), entry,
                probe_version=_probe_version, health_check=_health_check,
                forbidden_roots=(extension_root,),
                apply_overlay=apply_solver_overlay)
        self.target_entry = entry
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


def _read_registry() -> tuple[SolverRegistry, str | None]:
    try:
        return load_registry(ManagedSolverPaths.default().registry_json), None
    except ValueError as exc:
        return SolverRegistry(), str(exc)


def _solver_session_active() -> bool:
    try:
        from ..bake.controller import shared_controller
        return bool(shared_controller.snapshot().active)
    except (ImportError, AttributeError):
        return False


def _solver_enum_items(_self, _context):
    registry, _error = _read_registry()
    items = []
    for index, installation in enumerate(registry.installations):
        status = (
            f"Protocol {installation.protocol_version or '?'} · "
            f"Schema {installation.schema_version or '?'} · "
            f"{'Managed' if installation.managed else 'External'}")
        items.append((
            installation.installation_id,
            installation.display_name,
            status,
            "CHECKMARK" if installation.healthy else "ERROR",
            index))
    if not items:
        items.append(("NONE", "No Solver Selected",
                      "Install or register a compatible solver", "ERROR", 0))
    return items


def _solver_enum_update(self, _context):
    selected = str(getattr(self, "selected_solver_installation_id", "") or "")
    if selected == "NONE" or _solver_session_active():
        return
    paths = ManagedSolverPaths.default()
    registry, error = _read_registry()
    if error or registry.get(selected) is None:
        return
    try:
        write_registry(paths.registry_json, registry.select(selected))
    except (OSError, ValueError):
        return


class CLOTHNEXT_OT_solver_use(bpy.types.Operator):
    """Use this healthy compatible installation for future solver work"""
    bl_idname = "clothnext.solver_use"
    bl_label = "Use"
    bl_description = (
        "Make this verified installation the active solver for new Bakes, "
        "tests, updates and compatible recovery")
    bl_options = {"INTERNAL"}

    installation_id: bpy.props.StringProperty()

    @classmethod
    def poll(cls, _context):
        return not _solver_session_active()

    def execute(self, context):
        paths = ManagedSolverPaths.default()
        registry, error = _read_registry()
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        try:
            registry = registry.select(self.installation_id)
            write_registry(paths.registry_json, registry)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        addon_preferences(
            context, __package__).selected_solver_installation_id = self.installation_id
        self.report({"INFO"}, "Active solver installation changed.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_refresh_installations(bpy.types.Operator):
    """Refresh registered installations and migrate the previous 0.11 setup"""
    bl_idname = "clothnext.solver_refresh_installations"
    bl_label = "Refresh Installations"
    bl_description = (
        "Refresh installation paths and verify the legacy managed solver "
        "registry without downloading or changing the active solver")
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        def refresh():
            manifest = load_bundled_manifest()
            migrate_legacy_current(
                ManagedSolverPaths.default(), manifest,
                probe_version=_probe_version, health_check=_health_check)
        _run_in_worker(refresh, status="Refreshing solver installations")
        self.report({"INFO"}, "Refreshing solver installations in the background.")
        return {"FINISHED"}


def _installer_state() -> InstallerState:
    """Local session state; never touches the network or starts anything.

    An older or legacy managed installation is reported as UPDATE_AVAILABLE
    immediately — comparing ``current.json`` with the bundled compatibility
    manifest needs no GitHub request, no solver process, and no thread.
    """
    installer = _session.installer
    if installer is not None:
        return installer.state
    active, valid = _safe_read_current()
    if not valid:
        return InstallerState.REPAIR_REQUIRED
    if active is not None:
        if solver_update_available(active, _session.entry):
            return InstallerState.UPDATE_AVAILABLE
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
        _session.entry.schema_version, release_label=active.release_label)


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
    solver_active = bool(worker is not None and worker.is_alive())
    if not solver_active:
        registry, _error = _read_registry()
        selected = registry.selected_installation_id
        if selected:
            try:
                preferences = addon_preferences(bpy.context, __package__)
                preferences.selected_solver_installation_id = selected
            except (KeyError, AttributeError):
                pass
        return None
    return 0.25


def _run_in_worker(target, *, status: str | None = None) -> None:
    if _session.worker is not None and _session.worker.is_alive():
        return
    _session.worker_status = status
    _session.worker_error = None

    def guarded_target():
        try:
            target()
        except (OSError, ValueError, RuntimeError) as exc:
            _session.worker_error = str(exc)
        finally:
            _session.worker_status = None

    _session.worker = threading.Thread(target=guarded_target, daemon=True,
                                       name="clothnext-solver-installer")
    _session.worker.start()
    if not bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.register(_ui_refresh_pulse, first_interval=0.25)


def shutdown(join_timeout: float = 10.0) -> bool:
    """Cancel installer work without forgetting a worker still winding down."""
    installer = _session.installer
    if installer is not None:
        installer.cancel()
    worker = _session.worker
    if worker is not None and worker.is_alive():
        worker.join(timeout=max(0.0, float(join_timeout)))
    stopped = worker is None or not worker.is_alive()
    if bpy.app.timers.is_registered(_ui_refresh_pulse):
        bpy.app.timers.unregister(_ui_refresh_pulse)
    if not stopped:
        return False
    _session.worker = None
    _session.installer = None
    _session.entry = None
    _session.entries = ()
    _session.target_entry = None
    _session.worker_status = None
    _session.worker_error = None
    _session.disabled_reason = None
    _session.loaded = False
    return True


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
        entry = _session.target_entry or _session.entry
        if entry is None:
            return
        for line in view_model.confirmation_lines(entry, ManagedSolverPaths.default()):
            layout.label(text=line)
        row = layout.row()
        row.operator("clothnext.solver_open_download_page", text="View Official Source")
        row.operator("clothnext.solver_view_licenses", text="View License Information")
        layout.label(text="Click OK to download and install; press Esc to cancel.")

    def execute(self, _context):
        # Keep using the release selected by invoke(). Calling the default
        # installer here would replace a confirmed 0.13 installer with the
        # manifest's default 0.11 installer and lose the confirmation state.
        release_id = str(getattr(self, "release_id", "") or "")
        installer = _session.ensure_installer(release_id)
        if installer is None:
            return {"CANCELLED"}
        if installer.state is not InstallerState.AWAITING_CONFIRMATION:
            # Never crash the worker with an invalid transition; tell the user.
            self.report({"WARNING"}, "The download was not confirmed; "
                        "nothing was started.")
            return {"CANCELLED"}
        activate = _session.activate_after_install
        _run_in_worker(lambda: installer.install(
            confirmed=True, activate=activate, reinstall=_session.reinstall),
            status="Installing solver release")
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
    bl_description = (
        "Download and verify this exact official solver release without "
        "overwriting any other installed release")

    release_id: bpy.props.StringProperty(default="")
    activate_after_install: bpy.props.BoolProperty(default=False)
    reinstall: bpy.props.BoolProperty(default=False)

    def invoke(self, context, _event):
        if _solver_session_active():
            self.report({"ERROR"},
                        "Stop the active Bake before installing another solver.")
            return {"CANCELLED"}
        if self._report_online_access_blocked():
            return {"CANCELLED"}
        installer = _session.ensure_installer(self.release_id)
        if installer is None:
            self.report({"ERROR"}, "Automatic download is disabled: "
                        f"{_session.disabled_reason}")
            return {"CANCELLED"}
        if _session.worker is not None and _session.worker.is_alive():
            self.report({"INFO"}, "A solver installation is already running.")
            return {"CANCELLED"}
        installer.request_download()
        _session.activate_after_install = self.activate_after_install
        _session.reinstall = self.reinstall
        return context.window_manager.invoke_props_dialog(self, width=520)


class CLOTHNEXT_OT_solver_cancel(bpy.types.Operator):
    """Cancel the running solver download"""
    bl_idname = "clothnext.solver_cancel"
    bl_label = "Cancel Download"
    bl_description = (
        "Cancel the current solver download; no partial installation is "
        "registered and existing installations remain untouched")
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        if _session.installer is not None:
            _session.installer.cancel()
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_select_existing(bpy.types.Operator):
    """Select an existing external solver installation (never modified)"""
    bl_idname = "clothnext.solver_select_existing"
    bl_label = "Select Existing Installation"
    bl_description = (
        "Register an existing solver executable without copying, deleting or "
        "modifying its external files")
    bl_options = {"INTERNAL"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    def invoke(self, context, _event):
        if _solver_session_active():
            self.report({"ERROR"},
                        "Stop the active Bake before registering another solver.")
            return {"CANCELLED"}
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, _context):
        from ..updater.external import validate_external_installation
        _session.load()
        if not _session.entries:
            self.report({"ERROR"}, "No compatibility manifest entry is available.")
            return {"CANCELLED"}
        selected_path = Path(str(self.filepath))

        def register_external():
            package, protocol, schema = _probe_version(
                selected_path if selected_path.is_file()
                else BundledSolverLayout.from_root(
                    selected_path).executable_path)
            matching = next((entry for entry in _session.entries
                             if entry.protocol_version == protocol
                             and entry.schema_version == schema), None)
            if matching is None:
                raise ValueError(
                    f"unsupported protocol {protocol} / schema {schema}")
            result = validate_external_installation(
                selected_path, _probe_version, matching)
            if not result.compatible:
                raise ValueError(
                    f"unsupported protocol {result.protocol_version} / "
                    f"schema {result.schema_version}")
            healthy = _health_check(result.executable)
            paths = ManagedSolverPaths.default()
            registry = load_registry(paths.registry_json)
            existing = registry.find_executable(result.executable)
            if existing is not None:
                return
            installation = SolverInstallation(
                installation_id=external_installation_id(),
                display_name=f"Custom PPF {result.package_version}",
                source="external", root_path=str(result.root),
                executable_path=str(result.executable),
                frontend_path=str(result.root / "frontend"),
                package_version=result.package_version,
                protocol_version=result.protocol_version,
                schema_version=result.schema_version,
                official_release_tag=None,
                managed=False, verified=True, healthy=healthy,
                channel=("current" if healthy else "unsupported"),
                error=None if healthy else "Real solver health check failed")
            write_registry(paths.registry_json, registry.register(installation))

        _run_in_worker(register_external,
                       status="Testing and registering external solver")
        self.report({"INFO"}, "Testing the external solver in the background.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_open_download_page(bpy.types.Operator):
    """Open the official st-tech/ppf-contact-solver release page"""
    bl_idname = "clothnext.solver_open_download_page"
    bl_label = "Open Official Download Page"
    bl_description = "Open the immutable official release page for this solver"
    bl_options = {"INTERNAL"}
    release_id: bpy.props.StringProperty(default="")

    def execute(self, _context):
        _session.load()
        entry = next((item for item in _session.entries
                      if item.release_id == self.release_id), _session.entry)
        url = (entry.official_release_page if entry
               else "https://github.com/st-tech/ppf-contact-solver/releases")
        webbrowser.open(url)
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_view_licenses(bpy.types.Operator):
    """Open the upstream license information"""
    bl_idname = "clothnext.solver_view_licenses"
    bl_label = "View License Information"
    bl_description = "Open the official upstream solver license information"
    bl_options = {"INTERNAL"}

    def execute(self, _context):
        webbrowser.open("https://github.com/st-tech/ppf-contact-solver/blob/main/LICENSE")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_health_check(bpy.types.Operator):
    """Run the real health check against the active installation"""
    bl_idname = "clothnext.solver_health_check"
    bl_label = "Run Health Check"
    bl_description = (
        "Start this installation briefly and verify its executable, protocol, "
        "schema and status response")
    bl_options = {"INTERNAL"}
    installation_id: bpy.props.StringProperty(default="")

    def execute(self, _context):
        registry, error = _read_registry()
        installation = registry.get(
            self.installation_id or registry.selected_installation_id)
        if error or installation is None:
            self.report({"ERROR"}, error or "The installation is not registered.")
            return {"CANCELLED"}
        def test_and_store():
            healthy = _health_check(installation.executable)
            paths = ManagedSolverPaths.default()
            current = load_registry(paths.registry_json)
            latest = current.get(installation.installation_id)
            if latest is None:
                raise ValueError("The installation was removed during its health check.")
            updated = replace(
                latest, healthy=healthy,
                error=None if healthy else "Real solver health check failed")
            write_registry(paths.registry_json, current.update(updated))

        _run_in_worker(test_and_store, status="Testing solver installation")
        self.report({"INFO"}, "Health check started in the background.")
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_check_update(bpy.types.Operator):
    """Check whether a manifest-verified compatible update exists"""
    bl_idname = "clothnext.solver_check_update"
    bl_label = "Check for Compatible Update"
    bl_description = (
        "Refresh the official release availability without changing the "
        "active solver installation")
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
    bl_description = (
        "Reinstall only the selected managed release from its verified "
        "official archive while preserving every other solver version")
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
    bl_description = (
        "Remove only this managed solver directory, or unregister an external "
        "installation without deleting its files")
    bl_options = {"INTERNAL"}
    installation_id: bpy.props.StringProperty(default="")

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, _context):
        if _solver_session_active():
            self.report({"ERROR"}, "Stop the active Bake before removing a solver.")
            return {"CANCELLED"}
        paths = ManagedSolverPaths.default()
        registry, error = _read_registry()
        installation = registry.get(
            self.installation_id or registry.selected_installation_id)
        if error or installation is None:
            self.report({"ERROR"}, error or "Installation is not registered.")
            return {"CANCELLED"}
        try:
            if installation.managed:
                installer = _session.ensure_installer()
                if installer is None:
                    raise ValueError("No managed installer is available")
                installer.remove(installation.installation_id)
            registry = registry.unregister(installation.installation_id)
            write_registry(paths.registry_json, registry)
        except (OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, (
            "Managed installation removed." if installation.managed
            else "External installation unregistered; its files were untouched."))
        return {"FINISHED"}


class CLOTHNEXT_OT_solver_open_folder(bpy.types.Operator):
    """Open the managed solver installation folder"""
    bl_idname = "clothnext.solver_open_folder"
    bl_label = "Open Installation Folder"
    bl_description = "Open the managed solver installations folder in File Explorer"
    bl_options = {"INTERNAL"}
    installation_id: bpy.props.StringProperty(default="")

    def execute(self, _context):
        import os
        if self.installation_id:
            registry, error = _read_registry()
            installation = registry.get(self.installation_id)
            if error or installation is None or not installation.root.is_dir():
                self.report({"ERROR"}, error or "The installation folder is missing.")
                return {"CANCELLED"}
            os.startfile(installation.root)
            return {"FINISHED"}
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
    selected_solver_installation_id: bpy.props.EnumProperty(
        name="Active Solver",
        items=_solver_enum_items, update=_solver_enum_update,
        description="Select the exact installed solver used by future Bakes; "
                    "selection is locked while a session is active")

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
        name="Developer Tools", default=False,
        description="Show internal solver tests and UI diagnostics in the "
                    "Cloth NeXt Cache panel.")
    auto_launch_bake_window: bpy.props.BoolProperty(
        name="Open Bake Window Automatically", default=True,
        description="Require the visible topmost Bake window before locking "
                    "Blender. When disabled, Bake runs in Blender without a "
                    "global modal workflow lock")
    show_bake_hud: bpy.props.BoolProperty(name="Show Resource Monitor", default=True)
    bake_hud_anchor: bpy.props.EnumProperty(name="HUD Anchor", items=(("TOP_LEFT", "Top Left", ""),("TOP_RIGHT", "Top Right", ""),("BOTTOM_LEFT", "Bottom Left", ""),("BOTTOM_RIGHT", "Bottom Right", "")), default="BOTTOM_LEFT")
    bake_hud_scale: bpy.props.FloatProperty(name="HUD Scale", default=1.0, min=0.75, max=2.0)
    telemetry_refresh_seconds: bpy.props.FloatProperty(name="Telemetry Refresh", default=1.0, min=0.25, max=10.0, subtype="TIME")
    auto_cancel_high_ram: bpy.props.BoolProperty(
        name="Auto-Cancel on High RAM", default=True,
        description="Cancel an active Bake after RAM stays above the safety "
                    "threshold for two telemetry samples")
    auto_cancel_ram_percent: bpy.props.IntProperty(
        name="RAM Safety Threshold", default=90, min=50, max=99,
        subtype="PERCENTAGE",
        description="Total system RAM usage that automatically cancels the Bake")

    def draw(self, context) -> None:
        layout = self.layout
        self._draw_addon_update_section(layout, context)
        self._draw_solver_section(layout)
        if is_dev_build():
            layout.prop(self, "developer_tools")
        layout.prop(self, "auto_launch_bake_window")
        hud_box=layout.box(); hud_box.label(text="Bake Resource Monitor")
        for name in ("show_bake_hud","bake_hud_anchor","bake_hud_scale","telemetry_refresh_seconds"): hud_box.prop(self,name)
        safety=hud_box.box(); safety.label(text="Memory Safety", **icon_registry.icon_kwargs("monitor", "MEMORY"))
        safety.prop(self,"auto_cancel_high_ram")
        threshold=safety.row(); threshold.enabled=getattr(
            self,"auto_cancel_high_ram",True)
        threshold.prop(self,"auto_cancel_ram_percent")

    def _draw_addon_update_section(self, layout, context) -> None:
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
        channel = addon_updates.UpdateChannel[self.update_channel]
        repos = context.preferences.extensions.repos
        if addon_updates.find_channel_repo(repos, channel) is None:
            box.operator("clothnext.addon_update_repo_setup",
                         text="Register Update Channel")
        if self.update_channel == "DEV":
            warning=box.box(); warning.label(text="Development Channel", **icon_registry.icon_kwargs("error", "ERROR"))
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
        if view.show_update_handoff:
            actions.operator("clothnext.addon_update_through_blender")
        elif view.show_open_extensions:
            actions.operator("clothnext.addon_open_extensions")
        actions.operator("clothnext.addon_open_release_notes")

    def _draw_solver_section(self, layout) -> None:
        box = layout.box()
        box.label(text="PPF Contact Solver")
        box.label(text="Solver Installations")
        _session.load()
        registry, registry_error = _read_registry()
        selected_id = (getattr(self, "selected_solver_installation_id", "")
                       or registry.selected_installation_id or "")
        active = registry.get(selected_id)
        session_active = _solver_session_active()
        box.label(text="Active Solver")
        selector = box.row()
        selector.enabled = not session_active and bool(registry.installations)
        selector.prop(self, "selected_solver_installation_id")
        if active is None:
            box.label(text="No Solver Selected", **icon_registry.icon_kwargs("error", "ERROR"))
            if selected_id:
                box.label(text="The selected installation is missing.")
        else:
            box.label(text=(
                f"{active.display_name} · Protocol "
                f"{active.protocol_version} · Schema {active.schema_version}"))
        if session_active:
            box.label(
                text="Solver selection is locked while a Bake is active.",
                icon="LOCKED")
        if registry_error:
            box.label(text=registry_error, **icon_registry.icon_kwargs("error", "ERROR"))

        installed_box = box.box()
        installed_box.label(text="Installed")
        if not registry.installations:
            installed_box.label(text="No registered solver installations.")
        for installation in registry.installations:
            card = installed_box.box()
            label = installation.display_name
            if installation.installation_id == selected_id:
                label += " · Active"
            card.label(text=label)
            card.label(text=(
                f"Protocol {installation.protocol_version or 'Unknown'} · "
                f"Schema {installation.schema_version or 'Unknown'} · "
                f"{installation.channel.title()}"))
            card.label(text=(
                f"Package {installation.package_version or 'Unknown'} · "
                f"Release {installation.official_release_tag or 'Unverified'}"))
            card.label(text=(
                ("Healthy" if installation.healthy else "Unhealthy")
                + " · " + ("Managed" if installation.managed else "External")))
            card.label(text=installation.root_path)
            actions = card.row()
            use = actions.row()
            use.enabled = (
                not session_active and installation.compatible
                and installation.healthy and installation.verified
                and installation.available
                and installation.installation_id != selected_id)
            operator = use.operator("clothnext.solver_use", text="Use")
            operator.installation_id = installation.installation_id
            test = actions.operator(
                "clothnext.solver_health_check", text="Test")
            test.installation_id = installation.installation_id
            folder = actions.operator(
                "clothnext.solver_open_folder", text="Open Folder")
            folder.installation_id = installation.installation_id
            remove = actions.operator(
                "clothnext.solver_remove_managed", text="Remove")
            remove.installation_id = installation.installation_id

        available_box = box.box()
        available_box.label(text="Available Downloads")
        installed_tags = {
            item.official_release_tag for item in registry.installations
            if item.managed}
        for entry in _session.entries:
            row_box = available_box.box()
            row_box.label(text=entry.display_name)
            row_box.label(text=(
                f"Protocol {entry.protocol_version} · Schema "
                f"{entry.schema_version} · {entry.channel.title()}"))
            if entry.official_release_tag in installed_tags:
                row_box.label(text="Installed", **icon_registry.icon_kwargs("success", "CHECKMARK"))
                reinstall_row = row_box.row()
                reinstall_row.enabled = not session_active
                reinstall = reinstall_row.operator(
                    "clothnext.solver_download", text="Reinstall")
                reinstall.release_id = entry.release_id
                reinstall.reinstall = True
            else:
                buttons = row_box.row()
                buttons.enabled = not session_active
                download = buttons.operator(
                    "clothnext.solver_download", text="Download")
                download.release_id = entry.release_id
                download.activate_after_install = False
                download_use = buttons.operator(
                    "clothnext.solver_download", text="Download and Use")
                download_use.release_id = entry.release_id
                download_use.activate_after_install = True
        installer = _session.installer
        if _session.worker_status:
            box.label(text=_session.worker_status, **icon_registry.icon_kwargs("timer", "TIME"))
        if _session.worker_error:
            box.label(text=_session.worker_error, **icon_registry.icon_kwargs("error", "ERROR"))
        if installer is not None:
            box.label(text=(
                "Installation stage: "
                f"{installer.state.name.replace('_', ' ').title()}"))
            if installer.state is InstallerState.DOWNLOADING:
                done, total = installer.download_progress
                box.label(text=view_model.format_download_progress(done, total))
            if installer.error is not None:
                box.label(text=installer.error.user_message, icon="ERROR")
            if (_session.worker is not None and _session.worker.is_alive()
                    and installer.state is InstallerState.DOWNLOADING):
                box.operator("clothnext.solver_cancel", text="Cancel")
        actions = box.row()
        actions.operator("clothnext.solver_select_existing")
        actions.operator("clothnext.solver_refresh_installations")
        actions.operator("clothnext.solver_open_download_page")
        return
        state = _installer_state()
        installer = _session.installer
        progress_text = None
        if installer is not None and state is InstallerState.DOWNLOADING:
            done, total = installer.download_progress
            progress_text = view_model.format_download_progress(done, total)
        section = view_model.build_section(state, _session.entry,
                                           _session.disabled_reason, _installed_info(),
                                           download_progress=progress_text)
        if section.update_alert is not None:
            # The primary visual message: a real Blender alert (red) box.
            # Drawing it never starts a download — the button below opens the
            # existing confirmation-gated installer dialog.
            warning = box.box()
            warning.alert = True
            warning.label(text=section.update_alert.title, icon="ERROR")
            for line in section.update_alert.lines:
                warning.label(text=line)
            install_row = warning.row()
            install_row.operator(
                _ACTION_OPERATORS[section.update_alert.action],
                text=section.update_alert.action_text)
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
            if (section.update_alert is not None
                    and action is section.update_alert.action):
                continue  # the alert box already offers this action once
            idname = _ACTION_OPERATORS.get(action)
            if idname is not None:
                actions.operator(idname)
        if self.external_solver_path:
            box.label(text=f"External installation: {self.external_solver_path}")


CLASSES = (
    CLOTHNEXT_OT_solver_use,
    CLOTHNEXT_OT_solver_refresh_installations,
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
