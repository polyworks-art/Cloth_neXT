# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fake-bpy tests for the red solver-update warning in the preferences.

The warning appears immediately when the preferences are drawn — comparing
``current.json`` with the bundled compatibility manifest is purely local, so
no click on "Check for Compatible Update", no network request, no thread, and
certainly no download is needed to show it.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cloth_next.updater.install_paths import ActiveInstallation
from cloth_next.updater.solver_registry import SolverInstallation, SolverRegistry

TAG = "2026-07-26-22-53"


class RecordingLayout:
    """FakeLayout that also records ``alert`` flags per sub-layout."""

    def __init__(self, log=None):
        self.log = log if log is not None else []
        self.enabled = True
        self._alert = False

    @property
    def alert(self):
        return self._alert

    @alert.setter
    def alert(self, value):
        self._alert = value
        self.log.append(("alert", bool(value)))

    def box(self):
        self.log.append(("box",))
        return RecordingLayout(self.log)

    def row(self, align=False):
        return RecordingLayout(self.log)

    def column(self, align=False):
        return RecordingLayout(self.log)

    def label(self, text="", icon=None):
        self.log.append(("label", text, icon))

    def operator(self, idname, text=None, icon=None):
        self.log.append(("operator", idname, text))
        return SimpleNamespace()

    def prop(self, _data, prop_name):
        self.log.append(("prop", prop_name))


def legacy_active():
    return ActiveInstallation(
        metadata_version=1, installation_id="0.1.0",
        solver_package_version="0.1.0",
        executable_relative="ppf-cts-server.exe", activated_at="")


def current_active():
    from cloth_next.updater.solver_manifest import load_bundled_manifest
    entry = load_bundled_manifest().entry_for("windows-x86_64")
    return ActiveInstallation(
        metadata_version=2, installation_id=entry.official_release_tag,
        solver_package_version=entry.solver_package_version,
        executable_relative="ppf-cts-server.exe", activated_at="",
        official_release_tag=entry.official_release_tag,
        official_asset_name=entry.official_asset_name,
        asset_sha256=entry.sha256)


def draw_preferences(env, monkeypatch, active, valid=True):
    import cloth_next.blender.preferences as preferences
    monkeypatch.setattr(preferences, "_safe_read_current",
                        lambda: (active, valid))
    if active is None:
        registry = SolverRegistry()
    else:
        installation = SolverInstallation(
            installation_id=active.installation_id,
            display_name="PPF Contact Solver",
            source="official", root_path="C:/solver",
            executable_path="C:/solver/ppf-cts-server.exe",
            frontend_path="C:/solver/frontend",
            package_version=active.solver_package_version,
            protocol_version="0.13", schema_version="2",
            official_release_tag=active.official_release_tag,
            managed=True, verified=True, healthy=True, channel="stable")
        registry = SolverRegistry(
            (installation,), installation.installation_id)
    monkeypatch.setattr(preferences, "_read_registry",
                        lambda: (registry, None))
    monkeypatch.setattr(preferences, "_solver_session_active", lambda: False)
    prefs = preferences.CLOTHNEXT_AddonPreferences()
    prefs.layout = RecordingLayout()
    prefs.external_solver_path = ""
    prefs.selected_solver_installation_id = (
        registry.selected_installation_id or "NONE")
    prefs.update_channel = "BETA"
    prefs.draw(env.bpy.context)
    return preferences, prefs.layout.log


def alert_flags(log):
    return [value for entry in log if entry[0] == "alert"
            for value in [entry[1]]]


def labels(log):
    return [text for entry in log if entry[0] == "label" for text in [entry[1]]]


def operators(log):
    return [(idname, text) for entry in log if entry[0] == "operator"
            for idname, text in [(entry[1], entry[2])]]


def test_legacy_installation_draws_red_alert_immediately(blender_env, monkeypatch):
    _prefs, log = draw_preferences(blender_env, monkeypatch, legacy_active())
    shown = labels(log)
    assert "Installed" in shown
    assert "Available Downloads" in shown
    assert any("Protocol 0.13" in text for text in shown)


def test_alert_button_uses_the_confirmation_gated_installer(blender_env, monkeypatch):
    _prefs, log = draw_preferences(blender_env, monkeypatch, legacy_active())
    ops = operators(log)
    assert ("clothnext.solver_download", "Download") in ops
    assert ("clothnext.solver_download", "Download and Use") in ops


def test_up_to_date_installation_shows_no_alert(blender_env, monkeypatch):
    _prefs, log = draw_preferences(blender_env, monkeypatch, current_active())
    assert True not in alert_flags(log)
    assert "Solver Update Available" not in labels(log)


def test_not_installed_shows_no_false_update_alert(blender_env, monkeypatch):
    _prefs, log = draw_preferences(blender_env, monkeypatch, None)
    assert True not in alert_flags(log)
    assert "Solver Update Available" not in labels(log)


def test_repair_required_still_shows_repair_flow(blender_env, monkeypatch):
    _prefs, log = draw_preferences(blender_env, monkeypatch, None, valid=False)
    assert "No Solver Selected" in labels(log)


def test_draw_starts_no_download_no_worker_no_installer(blender_env, monkeypatch):
    preferences, _log = draw_preferences(blender_env, monkeypatch, legacy_active())
    assert preferences._session.installer is None
    assert preferences._session.worker is None
    assert blender_env.bpy.app.timers.functions == []
    assert blender_env.bpy.ops_log == []


def test_session_load_starts_no_download(blender_env, monkeypatch):
    import cloth_next.blender.preferences as preferences
    monkeypatch.setattr(preferences, "_safe_read_current",
                        lambda: (legacy_active(), True))
    monkeypatch.setattr(preferences.ManagedSolverPaths, "default",
                        classmethod(lambda cls: cls(
                            Path(blender_env.bpy.app.tempdir) / "empty-managed")))
    preferences._session.load()
    assert preferences._session.entry is not None
    assert preferences._session.installer is None
    assert preferences._session.worker is None
    assert preferences._installer_state().name == "UPDATE_AVAILABLE"


def test_installed_and_available_release_rows_are_shown(blender_env, monkeypatch):
    _prefs, log = draw_preferences(blender_env, monkeypatch, current_active())
    shown = labels(log)
    assert "Installed" in shown
    assert "Available Downloads" in shown
    assert any("Release 2026-07-26-22-53" in text for text in shown)


def test_download_needs_the_confirmation_dialog_even_from_the_alert(
        blender_env, monkeypatch, tmp_path):
    """Clicking the alert button invokes the download operator, which always
    opens the confirmation dialog first; execute() without prior confirmed
    invoke never starts a worker."""
    import cloth_next.blender.preferences as preferences
    monkeypatch.setattr(preferences, "_safe_read_current",
                        lambda: (legacy_active(), True))
    from cloth_next.updater.install_paths import ManagedSolverPaths
    monkeypatch.setattr(ManagedSolverPaths, "default",
                        classmethod(lambda cls: cls(tmp_path / "managed")))
    operator = preferences.CLOTHNEXT_OT_solver_download()
    result = operator.execute(SimpleNamespace(window_manager=None))
    assert result == {"CANCELLED"}
    assert preferences._session.worker is None
