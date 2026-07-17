# SPDX-License-Identifier: GPL-3.0-or-later

"""Bake-window details and non-modal About gag."""

import inspect

from cloth_next.bake.status import BakeSnapshot
from companion import app


def test_details_meta_collects_useful_snapshot_facts():
    snapshot=BakeSnapshot(active_object_name="Cape",solver_mode="MANAGED",
        solver_version="1.2.3",estimated_remaining_seconds=65,
        error_code="CNX-E180")
    assert app.details_meta(snapshot)==(
        "Cape  ·  MANAGED 1.2.3  ·  Remaining ~01:05  ·  CNX-E180")


def test_about_gag_is_a_hover_tooltip_not_a_dialog():
    source=inspect.getsource(app.BakeWindow._build)
    assert app.ABOUT_TOOLTIP=="SideFX, please don’t sue me."
    assert "HoverTooltip(about,ABOUT_TOOLTIP)" in source
    assert "messagebox" not in inspect.getsource(app)


def test_details_replaces_nonfunctional_pause_control():
    source=inspect.getsource(app.BakeWindow)
    assert 'text="Details"' in source
    assert "_toggle_details" in source
    assert "def _pause" not in source
    assert "self.pause" not in source
