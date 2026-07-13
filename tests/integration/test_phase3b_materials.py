# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in real-solver proof of the Phase-3B material mapping.

Runs the full encode/upload/build/simulate/fetch slice against the pinned
real PPF solver for two calibrated fabrics (COTTON and DENIM) plus one
no-contact case, using the exact production encoder and session service.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from tools.run_ppf_vertical_slice import run


def _solver() -> Path:
    configured = os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE")
    if not configured:
        pytest.skip("CLOTH_NEXT_PPF_EXECUTABLE is not configured")
    executable = Path(configured)
    if not executable.is_file():
        pytest.fail(f"configured solver does not exist: {executable}")
    return executable


@pytest.mark.integration
def test_cotton_and_denim_produce_distinct_valid_results(tmp_path):
    executable = _solver()
    cotton = run(executable, tmp_path / "cotton", preset="COTTON")
    denim = run(executable, tmp_path / "denim", preset="DENIM")

    for report in (cotton, denim):
        assert report["result"] == "PASS"
        assert report["blender_frames"] == 8
        assert report["solver_frames_fetched"] == list(range(1, 8))
        assert report["cloth_vertices"] == 121
        assert report["pc2_header"]["vertex_count"] == 121
        assert report["pc2_header"]["frame_count"] == 8
        assert report["scene_disable_contact"] is False
        # final vertex positions are finite (validated in run(), spot-check)
        assert all(math.isfinite(c) for p in
                   report["final_frame_positions"] for c in p)

    # the uploaded parameter payloads differ exactly as the presets say
    assert cotton["shell_params"]["young-mod"] == 5500.0
    assert denim["shell_params"]["young-mod"] == 10000.0
    assert cotton["shell_params"]["strain-limit"] == \
        pytest.approx(0.05, abs=1e-9)
    assert denim["shell_params"]["strain-limit"] == \
        pytest.approx(0.03, abs=1e-9)
    assert cotton["param_hash"] != denim["param_hash"]
    # both simulations moved, and did not produce byte-identical frames
    assert cotton["max_cloth_displacement_m"] > 0.01
    assert denim["max_cloth_displacement_m"] > 0.01
    assert cotton["frame_sequence_sha256"] != denim["frame_sequence_sha256"]


@pytest.mark.integration
def test_no_contact_case_encodes_disable_contact_true(tmp_path):
    executable = _solver()
    report = run(executable, tmp_path / "nocontact", preset="DEFAULT_CLOTH",
                 contact_enabled=False)
    assert report["result"] == "PASS"
    assert report["scene_disable_contact"] is True
    assert report["solver_frames_fetched"] == list(range(1, 8))
    # with contact disabled the cloth free-falls: it must move farther than
    # the contact run would allow at the collider surface
    assert report["max_cloth_displacement_m"] > 0.05


@pytest.mark.integration
def test_real_ppf_produces_more_than_eight_frames(tmp_path):
    report = run(_solver(), tmp_path / "twenty_frames", frame_count=20)
    assert report["result"] == "PASS"
    assert report["blender_frames"] == 20
    assert report["solver_frames_fetched"] == list(range(1, 20))
    assert report["pc2_header"]["frame_count"] == 20
