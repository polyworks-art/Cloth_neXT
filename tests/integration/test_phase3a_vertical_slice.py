# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in proof of the complete Phase-3A PPF vertical slice."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
from tools.run_ppf_vertical_slice import run

@pytest.mark.integration
def test_real_ppf_vertical_slice(tmp_path):
    configured = os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE")
    if not configured:
        pytest.skip("CLOTH_NEXT_PPF_EXECUTABLE is not configured")
    executable = Path(configured)
    if not executable.is_file():
        pytest.fail(f"configured solver does not exist: {executable}")
    report = run(executable, tmp_path)
    assert report["result"] == "PASS"
    assert report["blender_frames"] == 8
    assert report["solver_frames_fetched"] == list(range(1, 8))
    assert report["cloth_vertices"] == 121
    assert report["max_cloth_displacement_m"] > 0.01
    assert report["pc2_header"]["vertex_count"] == 121
    assert report["pc2_header"]["frame_count"] == 8
    assert report["status_transitions"][-1] == "NO_DATA"
