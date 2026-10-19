# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compact solver preferences UI.

Layout: the active engine with its Bake options on top, one row per engine
(AETHER, GAIA) with its install/update action and the GAIA account state, and
the technical details in a collapsed sub-panel. Releases outside the current
compatibility matrix stay hidden; reinstall, switching between releases and
cleanup live in the Manage menu.
"""

from __future__ import annotations

import bpy

from ..updater.states import InstallerState
from . import icon_registry, preferences as _preferences


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


def _supported_installations(registry):
    return tuple(item for item in registry.installations if item.compatible)


def _worker_active() -> bool:
    worker = _preferences._session.worker
    return bool(worker is not None and worker.is_alive())


def _selected_installation(preferences, registry):
    selected_id = registry.selected_installation_id or ""
    return selected_id, registry.get(selected_id)


def _draw_worker_state(layout) -> None:
    if _preferences._session.worker_status:
        layout.label(text=_preferences._session.worker_status, **icon_registry.icon_kwargs("timer", "TIME"))
    if _preferences._session.worker_error:
        layout.label(text=_preferences._session.worker_error, **icon_registry.icon_kwargs("error", "ERROR"))

    installer = _preferences._session.installer
    if installer is None:
        return
    if installer.state is InstallerState.DOWNLOADING:
        done, total = installer.download_progress
        layout.label(
            text=_preferences.view_model.format_download_progress(done, total),
            **icon_registry.icon_kwargs("download", "IMPORT"),
        )
        if (
            _preferences._session.worker is not None
            and _preferences._session.worker.is_alive()
        ):
            layout.operator("clothnext.solver_cancel", text="Cancel Download")
    if installer.error is not None:
        layout.label(text=installer.error.user_message, **icon_registry.icon_kwargs("error", "ERROR"))


_BACKEND_PROTOCOLS = frozenset({"0.18", "0.22", "0.23", "0.26"})
_ENGINE_LABEL_FACTOR = 0.2


def _sub_panel(layout, idname: str, title: str):
    """Collapsible sub-panel, closed by default; ``None`` while collapsed."""
    if not hasattr(layout, "panel"):
        column = layout.column()
        column.label(text=title)
        return column
    header, body = layout.panel(idname, default_closed=True)
    header.label(text=title)
    return body


def _engine_row(layout, name: str):
    """Engine name in a fixed-width left column; returns the right column."""
    split = layout.split(factor=_ENGINE_LABEL_FACTOR)
    split.label(text=name)
    return split.column(align=True)


def draw_solver_section(self, layout) -> None:
    """Active engine on top, both engines below, technical details collapsed."""
    from ..updater import engines
    box = layout.box()
    box.label(
        text="Solver Engine",
        **icon_registry.icon_kwargs("gaia_engine", "NONE"),
    )

    _preferences._session.load()
    registry, registry_error = _preferences._read_registry()
    supported = _supported_installations(registry)
    selected_id, active = _selected_installation(self, registry)
    if active is not None and not active.compatible:
        active = None
        selected_id = ""
    session_active = _preferences._solver_session_active()
    busy = session_active or _worker_active()
    engine = engines.engine_of(active) if active is not None else None

    if registry_error:
        box.label(text=registry_error, **icon_registry.icon_kwargs("error", "ERROR"))

    if supported:
        engine_row = box.row()
        engine_row.enabled = not busy
        engine_row.prop(self, "solver_engine", text="Engine")
        if active is not None:
            status_text, status_icon = _status(active)
            engine_row.label(text=status_text, icon=status_icon)

    if active is None:
        _draw_no_active(self, box, supported, selected_id, busy)
    else:
        _draw_active(self, box, active, engine, busy, session_active)

    _draw_worker_state(box)
    _draw_engines(box, registry, supported, active, busy)
    if active is not None:
        _draw_details(self, box, registry, active, engine, busy)


def _draw_no_active(self, box, supported, selected_id, busy) -> None:
    title = "No Solver Installed" if not supported else "No Solver Selected"
    box.label(text=title, **icon_registry.icon_kwargs("error", "ERROR"))
    if selected_id:
        box.label(text="The selected solver installation is missing.")
    if supported:
        selector = box.row()
        selector.enabled = not busy
        selector.prop(self, "selected_solver_installation_id", text="Release")
        box.menu("CLOTHNEXT_MT_solver_manage", text="Manage")
        return
    actions = box.row(align=True)
    preferred = _preferences._session.entry
    if preferred is not None:
        install = actions.operator("clothnext.solver_download", text="Install Solver")
        install.release_id = preferred.release_id
        install.activate_after_install = True
    actions.menu("CLOTHNEXT_MT_solver_manage", text="Manage")


def _draw_active(self, box, active, engine, busy, session_active) -> None:
    from ..updater import engines
    has_backend = active.protocol_version in _BACKEND_PROTOCOLS
    is_gaia = engine == engines.GAIA
    if has_backend or is_gaia:
        options = box.row()
        options.enabled = not busy
        if has_backend:
            options.prop(self, "solver_backend_choice", text="Backend")
        if is_gaia:
            options.prop(self, "gaia_reproducible_bake")

    # Hints appear only when they apply.
    if has_backend:
        from ..ppf.backend_selection import available_backend_choices
        choice = str(getattr(self, "solver_backend_choice", "AUTO") or "AUTO")
        available = available_backend_choices(active.protocol_version, active.root)
        if choice not in available:
            box.label(
                text=f"{choice} is not available in this build; choose Auto or an available backend.",
                icon="ERROR")
        if choice == "ROCM":
            box.label(text="ROCm: general AMD GPU support is not yet verified.", icon="INFO")
    if is_gaia and getattr(self, "gaia_reproducible_bake", False):
        box.label(text="Slower. Not yet covered: collision meshes, rods, rigid bodies.",
                  icon="INFO")
    if active.error:
        box.label(text=active.error, **icon_registry.icon_kwargs("error", "ERROR"))
    if session_active:
        box.label(text="Solver selection is locked while a Bake is active.", icon="LOCKED")

    actions = box.row(align=True)
    test_row = actions.row(align=True)
    test_row.enabled = not busy
    test = test_row.operator("clothnext.solver_health_check", text="Test Install")
    test.installation_id = active.installation_id
    actions.menu("CLOTHNEXT_MT_solver_manage", text="Manage")


def _draw_engines(box, registry, supported, active, busy) -> None:
    """One row per engine with what is installed and its install/update action."""
    from . import gaia_account
    box.separator()
    column = box.column()
    _draw_aether_row(_engine_row(column, "AETHER"), registry, supported, active, busy)
    column.separator()
    gaia = _engine_row(column, "GAIA")
    _draw_installed_gaia(gaia, registry, gaia_account.snapshot())
    gaia_account.draw_gaia_rows(gaia)


def _draw_installed_gaia(column, registry, state) -> None:
    """Name the installed build whenever the account rows below would not."""
    from ..updater import engines
    if state.available is not None and not state.busy:
        return
    build = engines.gaia_build_of(engines.best_installation(registry, engines.GAIA))
    if build is not None:
        column.label(text=f"{build.version} installed", icon="CHECKMARK")


def _draw_aether_row(column, registry, supported, active, busy) -> None:
    from ..updater import engines
    installed = engines.best_installation(registry, engines.AETHER)
    installed_tags = {
        item.official_release_tag for item in supported
        if item.managed and item.official_release_tag
        and engines.engine_of(item) == engines.AETHER
    }
    preferred = _preferences._session.entry
    offer = (preferred if preferred is not None and preferred.downloadable
             and preferred.official_release_tag not in installed_tags else None)
    row = column.row(align=True)
    if installed is not None:
        tag = installed.official_release_tag or f"protocol {installed.protocol_version or '?'}"
        row.label(text=f"{tag} installed", icon="CHECKMARK")
    elif offer is not None:
        row.label(text=f"{offer.official_release_tag} available", icon="IMPORT")
    else:
        row.label(text="Not installed", icon="INFO")
    if offer is None:
        return
    action = row.row(align=True)
    action.enabled = not busy
    operator = action.operator(
        "clothnext.solver_download",
        text="Update AETHER" if installed is not None else "Install AETHER",
    )
    operator.release_id = offer.release_id
    # Never switches an active selection; only fills an empty one.
    operator.activate_after_install = active is None


def _draw_details(self, box, registry, active, engine, busy) -> None:
    from ..updater import engines
    body = _sub_panel(box, "clothnext_solver_details", "Details")
    if body is None:
        return
    if len(engines.installations_for(registry, engine)) > 1:
        selector = body.row()
        selector.enabled = not busy
        selector.prop(self, "selected_solver_installation_id", text="Release")
    body.label(text=active.display_name)
    body.label(text=engines.ENGINE_DESCRIPTIONS[engine])
    source = "Managed" if active.managed else "External"
    body.label(
        text=(
            f"Protocol {active.protocol_version or 'Unknown'} · "
            f"Schema {active.schema_version or 'Unknown'} · {source}"
        )
    )
    if active.protocol_version == "0.18":
        body.label(text="Lumen provides CUDA only; Auto uses CUDA.")
    if active.protocol_version in _BACKEND_PROTOCOLS:
        body.label(text="The selected backend is checked before each Bake.")


class CLOTHNEXT_MT_solver_manage(bpy.types.Menu):
    """Secondary solver release and maintenance actions."""

    bl_idname = "CLOTHNEXT_MT_solver_manage"
    bl_label = "Manage Solver"

    def draw(self, _context) -> None:
        layout = self.layout
        _preferences._session.load()
        registry, registry_error = _preferences._read_registry()
        supported = _supported_installations(registry)
        active = registry.selected
        if active is not None and not active.compatible:
            active = None
        session_active = _preferences._solver_session_active()
        busy = session_active or _worker_active()

        if registry_error:
            layout.label(text=registry_error, icon="ERROR")
            layout.separator()

        if supported:
            layout.label(text="Installed Releases")
            for installation in supported:
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

        layout.label(text="Official AETHER Releases")
        installed_by_tag = {
            installation.official_release_tag: installation
            for installation in supported
            if installation.managed and installation.official_release_tag
        }
        for entry in (item for item in _preferences._session.entries
                      if item.downloadable):
            installed = installed_by_tag.get(entry.official_release_tag)
            release_row = layout.row()
            release_row.enabled = not busy
            if installed is None:
                operator = release_row.operator(
                    "clothnext.solver_download",
                    text=f"Install AETHER {entry.official_release_tag}",
                )
                operator.release_id = entry.release_id
                preferred = _preferences._session.entry
                operator.activate_after_install = (
                    active is None and preferred is not None
                    and entry.release_id == preferred.release_id)
            else:
                operator = release_row.operator(
                    "clothnext.solver_download",
                    text=f"Reinstall AETHER {entry.official_release_tag}",
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
