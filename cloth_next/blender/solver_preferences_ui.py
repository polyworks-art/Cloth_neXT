# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compact solver preferences UI.

The solver registry intentionally continues to support side-by-side releases
while protocol 0.11 and 0.13 are both needed. The normal preferences view only
shows the active release; switching, installing, repairing, and cleanup live in
a single Manage menu instead of permanent installation and download lists.
"""

from __future__ import annotations

import bpy

from ..updater.states import InstallerState
from . import preferences as _preferences


def _status(installation) -> tuple[str, str]:
    """Return a compact human-facing status and Blender icon."""
    if not installation.available:
        return "Missing Files", "ERROR"
    if not installation.compatible:
        return "Incompatible", "ERROR"
    if not installation.verified:
        return "Unverified", "ERROR"
    if not installation.healthy:
        return "Needs Attention", "ERROR"
    return "Ready", "CHECKMARK"


def _is_selectable(installation) -> bool:
    return bool(
        installation.available
        and installation.compatible
        and installation.verified
        and installation.healthy
    )


def _worker_active() -> bool:
    worker = _preferences._session.worker
    return bool(worker is not None and worker.is_alive())


def _selected_installation(preferences, registry):
    selected_id = (
        getattr(preferences, "selected_solver_installation_id", "")
        or registry.selected_installation_id
        or ""
    )
    if selected_id == "NONE":
        selected_id = ""
    return selected_id, registry.get(selected_id)


def _draw_worker_state(layout) -> None:
    if _preferences._session.worker_status:
        layout.label(text=_preferences._session.worker_status, icon="TIME")
    if _preferences._session.worker_error:
        layout.label(text=_preferences._session.worker_error, icon="ERROR")

    installer = _preferences._session.installer
    if installer is None:
        return
    if installer.state is InstallerState.DOWNLOADING:
        done, total = installer.download_progress
        layout.label(
            text=_preferences.view_model.format_download_progress(done, total),
            icon="IMPORT",
        )
        if (
            _preferences._session.worker is not None
            and _preferences._session.worker.is_alive()
        ):
            layout.operator("clothnext.solver_cancel", text="Cancel Download")
    if installer.error is not None:
        layout.label(text=installer.error.user_message, icon="ERROR")


def draw_solver_section(self, layout) -> None:
    """Draw one active solver card with secondary actions behind Manage."""
    box = layout.box()
    box.label(text="Solver")

    _preferences._session.load()
    registry, registry_error = _preferences._read_registry()
    selected_id, active = _selected_installation(self, registry)
    session_active = _preferences._solver_session_active()
    busy = session_active or _worker_active()

    if registry_error:
        box.label(text=registry_error, icon="ERROR")

    if active is None:
        title = (
            "No Solver Installed"
            if not registry.installations
            else "No Solver Selected"
        )
        box.label(text=title, icon="ERROR")
        if selected_id:
            box.label(text="The selected solver installation is missing.")

        if registry.installations:
            selector = box.row()
            selector.enabled = not busy
            selector.prop(self, "selected_solver_installation_id", text="Release")
            box.menu("CLOTHNEXT_MT_solver_manage", text="Manage")
        else:
            actions = box.row(align=True)
            preferred = _preferences._session.entry
            if preferred is not None:
                install = actions.operator(
                    "clothnext.solver_download", text="Install Solver"
                )
                install.release_id = preferred.release_id
                install.activate_after_install = True
            actions.menu("CLOTHNEXT_MT_solver_manage", text="Manage")
        _draw_worker_state(box)
        return

    name_row = box.row(align=True)
    name_row.label(text=active.display_name)
    status_text, status_icon = _status(active)
    name_row.label(text=status_text, icon=status_icon)

    source = "Managed" if active.managed else "External"
    box.label(
        text=(
            f"Protocol {active.protocol_version or 'Unknown'} · "
            f"Schema {active.schema_version or 'Unknown'} · {source}"
        )
    )

    if len(registry.installations) > 1:
        selector = box.row()
        selector.enabled = not busy
        selector.prop(self, "selected_solver_installation_id", text="Release")

    if active.error:
        box.label(text=active.error, icon="ERROR")
    if session_active:
        box.label(
            text="Solver selection is locked while a Bake is active.",
            icon="LOCKED",
        )

    actions = box.row(align=True)
    test_row = actions.row(align=True)
    test_row.enabled = not busy
    test = test_row.operator("clothnext.solver_health_check", text="Test")
    test.installation_id = active.installation_id
    actions.menu("CLOTHNEXT_MT_solver_manage", text="Manage")

    _draw_worker_state(box)


class CLOTHNEXT_MT_solver_manage(bpy.types.Menu):
    """Secondary solver release and maintenance actions."""

    bl_idname = "CLOTHNEXT_MT_solver_manage"
    bl_label = "Manage Solver"

    def draw(self, _context) -> None:
        layout = self.layout
        _preferences._session.load()
        registry, registry_error = _preferences._read_registry()
        active = registry.selected
        session_active = _preferences._solver_session_active()
        busy = session_active or _worker_active()

        if registry_error:
            layout.label(text=registry_error, icon="ERROR")
            layout.separator()

        if registry.installations:
            layout.label(text="Installed Releases")
            for installation in registry.installations:
                if installation.installation_id == registry.selected_installation_id:
                    layout.label(
                        text=f"{installation.display_name} · Active",
                        icon="CHECKMARK",
                    )
                    continue
                use_row = layout.row()
                use_row.enabled = not busy and _is_selectable(installation)
                use = use_row.operator(
                    "clothnext.solver_use",
                    text=f"Use {installation.display_name}",
                )
                use.installation_id = installation.installation_id
            layout.separator()

        layout.label(text="Compatible Releases")
        installed_by_tag = {
            installation.official_release_tag: installation
            for installation in registry.installations
            if installation.managed and installation.official_release_tag
        }
        for entry in _preferences._session.entries:
            installed = installed_by_tag.get(entry.official_release_tag)
            release_row = layout.row()
            release_row.enabled = not busy
            if installed is None:
                operator = release_row.operator(
                    "clothnext.solver_download",
                    text=f"Install {entry.display_name}",
                )
                operator.release_id = entry.release_id
                operator.activate_after_install = active is None
            else:
                operator = release_row.operator(
                    "clothnext.solver_download",
                    text=f"Reinstall {entry.display_name}",
                )
                operator.release_id = entry.release_id
                operator.reinstall = True
                operator.activate_after_install = False

        layout.separator()
        maintenance = layout.column()
        maintenance.enabled = not busy
        maintenance.operator(
            "clothnext.solver_select_existing", text="Select Existing Solver"
        )
        maintenance.operator(
            "clothnext.solver_refresh_installations", text="Refresh Installations"
        )
        layout.operator(
            "clothnext.solver_open_download_page", text="View Official Releases"
        )

        if active is None:
            return

        layout.separator()
        folder = layout.operator(
            "clothnext.solver_open_folder", text="Open Installation Folder"
        )
        folder.installation_id = active.installation_id
        remove_row = layout.row()
        remove_row.enabled = not busy
        remove = remove_row.operator(
            "clothnext.solver_remove_managed",
            text=("Remove Solver" if active.managed else "Unregister Solver"),
        )
        remove.installation_id = active.installation_id


CLASSES = (CLOTHNEXT_MT_solver_manage,)


_ORIGINAL_DRAW = getattr(
    _preferences.CLOTHNEXT_AddonPreferences._draw_solver_section,
    "_clothnext_original_solver_draw",
    _preferences.CLOTHNEXT_AddonPreferences._draw_solver_section,
)
draw_solver_section._clothnext_original_solver_draw = _ORIGINAL_DRAW


def install() -> None:
    """Install the compact renderer before AddonPreferences registration."""
    _preferences.CLOTHNEXT_AddonPreferences._draw_solver_section = draw_solver_section


def uninstall() -> None:
    """Restore the original class method for clean reload cycles."""
    if (
        _preferences.CLOTHNEXT_AddonPreferences._draw_solver_section
        is draw_solver_section
    ):
        _preferences.CLOTHNEXT_AddonPreferences._draw_solver_section = _ORIGINAL_DRAW
