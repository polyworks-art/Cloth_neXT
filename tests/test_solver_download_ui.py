# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for the "Download Official Solver" flow.

The download button used to do nothing: ``CLOTHNEXT_OT_solver_repair``
inherited from the already registered ``CLOTHNEXT_OT_solver_download``, which
corrupts Blender's RNA↔Python class mapping so the parent operator's
``invoke`` (the confirmation dialog) was silently skipped and the queued
install failed with an invalid state transition inside the worker thread.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from cloth_next.updater.download import DownloadCancelled
from cloth_next.updater.install_paths import ManagedSolverPaths
from cloth_next.updater.managed import ManagedSolverInstaller
from cloth_next.updater.states import InstallerState
from cloth_next.updater import view_model

from tests.test_phase27_hardening import make_entry

REPO_ROOT = Path(__file__).resolve().parents[1]
BLENDER_PACKAGE = REPO_ROOT / "cloth_next" / "blender"


# --- the root cause: registered-operator inheritance --------------------------------

def _operator_class_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if (isinstance(base, ast.Attribute) and base.attr == "Operator"
                    and isinstance(base.value, ast.Attribute)
                    and base.value.attr == "types"):
                names.add(node.name)
    return names


def test_no_operator_class_subclasses_another_registered_operator():
    """Registering a subclass of a registered Operator breaks the parent's
    invoke dispatch in Blender; share behavior through plain mixins instead."""
    for path in BLENDER_PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        operators = _operator_class_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                if isinstance(base, ast.Name) and base.id in operators:
                    raise AssertionError(
                        f"{path.name}: {node.name} subclasses registered "
                        f"operator {base.id}")


def test_repair_operator_is_not_a_download_subclass(blender_env):
    import cloth_next.blender.preferences as preferences
    assert not issubclass(preferences.CLOTHNEXT_OT_solver_repair,
                          preferences.CLOTHNEXT_OT_solver_download)
    # both still provide the shared confirmation dialog behavior
    for cls in (preferences.CLOTHNEXT_OT_solver_download,
                preferences.CLOTHNEXT_OT_solver_repair):
        assert issubclass(cls, preferences._SolverInstallDialog)
        assert "invoke" in vars(cls)


# --- download progress ----------------------------------------------------------------

def test_installer_wires_progress_into_fetch(tmp_path):
    def fetch(_entry, _destination, *, cancel=None, progress=None, **_kwargs):
        assert progress is not None, "installer must pass a progress callback"
        progress(123, 456)
        raise DownloadCancelled("stop for the test")

    installer = ManagedSolverInstaller(
        ManagedSolverPaths(tmp_path / "managed"), make_entry(),
        probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=fetch)
    installer.request_download()
    state = installer.install(confirmed=True)
    assert state is InstallerState.DOWNLOAD_AVAILABLE  # cancelled, nothing installed
    assert installer.download_progress == (123, 456)


def test_progress_starts_at_manifest_size(tmp_path):
    def fetch(_entry, _destination, **_kwargs):
        raise DownloadCancelled("immediately")

    entry = make_entry()
    installer = ManagedSolverInstaller(
        ManagedSolverPaths(tmp_path / "managed"), entry,
        probe_version=lambda _p: ("0.1.0", "0.11", "1"),
        health_check=lambda _p: True, fetch=fetch)
    installer.request_download()
    installer.install(confirmed=True)
    assert installer.download_progress == (0, entry.download_size)


def test_format_download_progress():
    assert view_model.format_download_progress(0, 0) == "0 MiB"
    text = view_model.format_download_progress(128 * 1024 * 1024, 512 * 1024 * 1024)
    assert text == "128 / 512 MiB (25%)"


def test_build_section_shows_progress_row_while_downloading():
    section = view_model.build_section(InstallerState.DOWNLOADING, make_entry(),
                                       None, None, download_progress="1 / 2 MiB (50%)")
    assert ("Progress", "1 / 2 MiB (50%)") in section.rows
    # other busy states carry no progress row
    section = view_model.build_section(InstallerState.VERIFYING, make_entry(),
                                       None, None, download_progress="1 / 2 MiB (50%)")
    assert all(label != "Progress" for label, _value in section.rows)


# --- UI refresh timer and online-access gate --------------------------------------------

def test_worker_registers_ui_refresh_timer_and_shutdown_removes_it(blender_env):
    import cloth_next.blender.preferences as preferences
    env = blender_env
    preferences._run_in_worker(lambda: None)
    assert preferences._ui_refresh_pulse in env.bpy.app.timers.functions
    preferences.shutdown()
    assert env.bpy.app.timers.functions == []
    assert preferences._session.worker is None


def test_refresh_pulse_stops_after_worker_finishes(blender_env):
    import cloth_next.blender.preferences as preferences
    preferences._run_in_worker(lambda: None)
    preferences._session.worker.join(timeout=10)
    assert preferences._ui_refresh_pulse() is None  # tells Blender to stop the timer
    preferences.shutdown()


def test_download_invoke_blocked_without_online_access(blender_env):
    import cloth_next.blender.preferences as preferences
    env = blender_env
    env.bpy.app.online_access = False
    operator = preferences.CLOTHNEXT_OT_solver_download()
    result = operator.invoke(SimpleNamespace(window_manager=None), None)
    assert result == {"CANCELLED"}
    assert any("ERROR" in levels for levels, _msg in operator.reports)
