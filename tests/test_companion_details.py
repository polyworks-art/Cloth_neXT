# SPDX-License-Identifier: GPL-3.0-or-later

"""Bake-window details and non-modal About gag."""

import inspect

from cloth_next.bake.status import BakeSnapshot, BakeState
from companion import app


def test_details_meta_collects_useful_snapshot_facts():
    snapshot=BakeSnapshot(active_object_name="Cape",solver_mode="MANAGED",
        solver_version="1.2.3",estimated_remaining_seconds=65,
        error_code="CNX-E180")
    assert app.details_meta(snapshot)==(
        "Object     Cape\nSolver     Managed · 1.2.3\n"
        "Error      CNX-E180")


def test_simulation_details_do_not_duplicate_the_progress_frame():
    snapshot = BakeSnapshot(
        state=BakeState.SIMULATING,
        status_message="Simulating frame 67 of 137")
    assert app.details_status(snapshot) == ""


def test_error_details_remain_visible_while_simulating():
    snapshot = BakeSnapshot(
        state=BakeState.SIMULATING,
        error_details="Stage: Solver\nCause: Contact overflow")
    assert app.details_status(snapshot) == (
        "Stage: Solver\nCause: Contact overflow")


def test_about_gag_is_a_hover_tooltip_not_a_dialog():
    source=inspect.getsource(app.BakeWindow._build)
    assert app.ABOUT_TOOLTIP=="SideFX, please don’t sue me."
    assert "HoverTooltip(about,ABOUT_TOOLTIP)" in source
    assert "messagebox" not in inspect.getsource(app)


def test_error_docs_link_accepts_only_stable_cnx_codes():
    assert app.error_docs_url("cnx-e161") == (
        "https://polyworks-art.github.io/Cloth_neXT/errors/#CNX-E161")
    assert app.error_docs_url("CNX-E!!") == ""
    assert app.error_docs_url("https://example.com") == ""


def test_existing_companion_transitions_to_bake_in_place():
    source=inspect.getsource(app.BakeWindow.enter_bake_mode)
    assert "already_visible" in source
    assert "if not already_visible" in source
    assert source.index("if not already_visible") < source.index(
        "self.root.deiconify()")
    assert source.index("self._center_on_screen()") < source.index(
        "self.root.deiconify()")


def test_companion_is_centered_before_first_visible_frame():
    source=inspect.getsource(app.BakeWindow.__init__)
    assert source.index("self.root.withdraw()") < source.index(
        "self._center_on_screen()")
    assert source.index("self._center_on_screen()") < source.index(
        "self.root.deiconify()")


def test_details_height_uses_requested_content_height():
    source=inspect.getsource(app.BakeWindow._fit_window_to_content)
    assert "self.root.winfo_reqheight()" in source
    assert "max(DETAILS_HEIGHT,requested)" in source


def test_details_graph_owns_normal_panel_and_eta_sits_below_it():
    source=inspect.getsource(app.BakeWindow._build)
    states=inspect.getsource(app.BakeWindow._show_performance_details)
    assert 'self.performance_section.pack(fill="both",expand=True)' in source
    assert 'height=92' in source
    assert 'self.performance_eta.pack(fill="x",pady=(4,0))' in source
    assert "self.diagnostics_section.pack_forget()" in states


def test_error_details_replace_graph_and_window_refits_after_updates():
    states=inspect.getsource(app.BakeWindow._show_performance_details)
    show=inspect.getsource(app.BakeWindow.show)
    assert "self.performance_section.pack_forget()" in states
    assert 'self.diagnostics_section.pack(fill="both",expand=True)' in states
    assert "self._fit_window_to_content()" in show


def test_details_replaces_nonfunctional_pause_control():
    source=inspect.getsource(app.BakeWindow)
    assert 'text="Details"' in source
    assert "_toggle_details" in source
    assert "def _pause" not in source
    assert "self.pause" not in source


def test_error_documentation_link_lives_in_details_foldout():
    source=inspect.getsource(app.BakeWindow)
    assert "self.error_docs_link" in source
    assert "error_docs_url(error_code)" in source
    assert "webbrowser.open(self._error_docs_url)" in source


def test_solver_project_build_is_not_labeled_as_running_simulation():
    assert app.ACTIVITY_LABELS[
        app.BakeActivity.BUILDING_CONTACTS] == "Building contact constraints"
    from cloth_next.bake.status import PHASE_ACTIVITIES
    assert PHASE_ACTIVITIES["BUILDING"] is app.BakeActivity.BUILDING_CONTACTS
