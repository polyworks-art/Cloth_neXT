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


def test_recovery_action_is_prioritized_in_compact_error_details():
    snapshot = BakeSnapshot(
        state=BakeState.ERROR,
        error_details=("Stage: Solve\nBlender frame: 89\nCause: PCG failed\n"
                       "What to do: Lower Friction first."))
    assert app.details_status(snapshot) == (
        "Stage: Solve\nBlender frame: 89\nWhat to do: Lower Friction first.")


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


def test_details_graph_owns_normal_panel_and_summary_footer_sits_below_it():
    source=inspect.getsource(app.BakeWindow._build)
    states=inspect.getsource(app.BakeWindow._show_performance_details)
    assert 'self.performance_section.pack(fill="both",expand=True)' in source
    assert 'height=92' in source
    assert 'self.performance_footer.pack(fill="x",pady=(4,0))' in source
    assert 'textvariable=self.frame_count_text' in source
    assert 'self.performance_eta.pack(side="right")' in source
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


def test_solver_statistics_row_owns_estimated_fill_without_new_row():
    source=inspect.getsource(app.BakeWindow._build)
    assert "self.status_fill=self.status.create_rectangle" in source
    assert "self.status.grid(row=1,column=0" in source
    assert source.count("self.status.grid(") == 1
    assert "CurrentFrameProgressEstimator" in inspect.getsource(app)


def test_solver_statistics_use_dedicated_colored_icons():
    source=inspect.getsource(app.BakeWindow._build)
    assert 'status_contacts_14.png' in source
    assert 'status_newton_14.png' in source
    assert 'status_iterations_14.png' in source
    parser=inspect.getsource(app.BakeWindow._solver_status_values)
    assert "contacts" in parser
    assert "Newton" in parser
    assert "linear iterations" in parser
    assert app.BakeWindow._solver_status_values(
        None,
        "Solver · 408 contacts · Newton 2 · 187 linear iterations") == (
            ("contacts","408"),("newton","2"),("iterations","187"))
    setter=inspect.getsource(app.BakeWindow._set_activity)
    assert "if not self._solver_status_values(value)" in setter


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
