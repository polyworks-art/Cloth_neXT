# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Opt-in real-solver coverage for Rod, Soft Body and Rigid Body sessions."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.run_ppf_rod_softbody import run


def _solver() -> Path:
    configured = os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE")
    if not configured:
        pytest.skip("CLOTH_NEXT_PPF_EXECUTABLE is not configured")
    executable = Path(configured)
    if not executable.is_file():
        pytest.fail(f"configured solver does not exist: {executable}")
    return executable


@pytest.mark.integration
def test_real_rod_and_soft_body_sessions(tmp_path):
    report = run(_solver(), tmp_path)

    assert report == {
        "result": "PASS",
        "ROD": {"frames": 4, "vertices": 5},
        "SOLID": {"frames": 4, "vertices": 6},
        "PDRD": {"frames": 4, "vertices": 4},
    }
