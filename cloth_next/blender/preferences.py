"""Add-on preferences: the "PPF Contact Solver" section and its operators.

The panel only renders what :mod:`cloth_next.updater.view_model` computes.
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

from cloth_next.ppf.compatibility import parse_executable_version
from cloth_next.updater import view_model
from cloth_next.updater.install_paths import ManagedSolverPaths, read_current
from cloth_next.updater.managed import ManagedSolverInstaller
from cloth_next.updater.modes import InstallationMode
from cloth_next.updater.solver_manifest import (SolverCompatibilityEntry,
                                                load_bundled_manifest)
from cloth_next.updater.states import InstallerAction, InstallerState

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
    from cloth_next.updater.health_runner import run_real_health_check
    return run_real_health_check(executable)


def _installer_state() -> InstallerState:
    installer = _session.installer
    if installer is not None:
        return installer.state
    if read_current(ManagedSolverPaths.default()) is not None:
        return InstallerState.READY
    return InstallerState.NOT_INSTALLED


def _installed_info() -> view_model.InstalledInfo | None:
    _session.load()
    paths = ManagedSolverPaths.default()
    active = read_current(paths)
    if active is None or _session.entry is None:
        return None
    return view_model.InstalledInfo(
        InstallationMode.MANAGED_INSTALLATION,
        active.version, _session.entry.protocol_version,
        _session.entry.schema_version)


def _run_in_worker(target) -> None:
    if _session.worker is not None and _session.worker.is_alive():
        return
    _session.worker = threading.Thread(target=target, daemon=True,
                                       name="clothnext-solver-installer")
    _session.worker.start()


class CLOTHNEXT_OT_solver_download(bpy.types.Operator):
    """Download the official PPF Contact Solver after explicit confirmation"""
    bl_idname = "clothnext.solver_download"
    bl_label = "Download Official Solver"
    bl_options = {"REGISTER", "INTERNAL"}

    def invoke(self, context, _event):
        installer = _session.ensure_installer()
        if installer is None:
            self.report({"ERROR"}, "Automatic download is disabled: "
                        f"{_session.disabled_reason}")
            return {"CANCELLED"}
        installer.request_download()
        return context.window_manager.invoke_props_dialog(self, width=520)

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
        _run_in_worker(lambda: installer.install(confirmed=True))
        self.report({"INFO"}, "Downloading the official solver in the background.")
        return {"FINISHED"}

    def cancel(self, _context):
        installer = _session.installer
        if installer is not None and installer.state is InstallerState.AWAITING_CONFIRMATION:
            installer.install(confirmed=False)


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
        from cloth_next.updater.external import validate_external_installation
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
        active = read_current(paths)
        if active is None:
            self.report({"ERROR"}, "No managed solver installation is active.")
            return {"CANCELLED"}
        executable = active.executable_path(paths)
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


class CLOTHNEXT_OT_solver_repair(CLOTHNEXT_OT_solver_download):
    """Repair the managed installation by reinstalling the verified official release"""
    bl_idname = "clothnext.solver_repair"
    bl_label = "Repair Managed Installation"

    def invoke(self, context, _event):
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
        active = read_current(ManagedSolverPaths.default())
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

    def draw(self, _context) -> None:
        layout = self.layout
        box = layout.box()
        box.label(text="PPF Contact Solver")
        _session.load()
        section = view_model.build_section(_installer_state(), _session.entry,
                                           _session.disabled_reason, _installed_info())
        for label, value in section.rows:
            row = box.row()
            row.label(text=f"{label}: {value}")
        if section.message:
            box.label(text=section.message)
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
