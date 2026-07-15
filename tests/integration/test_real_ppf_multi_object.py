# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.run_ppf_multi_object import run


@pytest.mark.integration
def test_real_multi_object_session(tmp_path):
    configured = os.environ.get("CLOTH_NEXT_PPF_EXECUTABLE")
    if not configured:
        pytest.skip("CLOTH_NEXT_PPF_EXECUTABLE is not configured")
    report = run(Path(configured), tmp_path)
    assert report["result"] == "PASS"
    assert report["frames"] == 3
    assert report["objects"] == ["multi-cloth-a", "multi-cloth-b"]
