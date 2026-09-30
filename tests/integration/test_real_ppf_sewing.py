# SPDX-License-Identifier: GPL-3.0-or-later
"""Sewing must close without anchoring the dynamic endpoints in world space."""

import os
from pathlib import Path

import pytest

from tools.run_ppf_sewing import run


@pytest.mark.integration
@pytest.mark.parametrize("mode", ("intra", "cross", "mixed"))
def test_sewn_unpinned_panels_close_and_free_fall(tmp_path, mode):
    executable = os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE")
    if not executable:
        pytest.skip("CLOTH_NEXT_PPF_EXECUTABLE is not configured")
    report = run(Path(executable), tmp_path / mode, mode=mode)
    assert report["frames"] == 12
    assert max(report["first_frame_seam_distance"]) < 0.003
