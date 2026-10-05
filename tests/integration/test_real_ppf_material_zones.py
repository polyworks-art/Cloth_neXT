# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import pytest
from tools.run_ppf_material_zones import run


@pytest.mark.integration
def test_exact_shared_vertex_material_boundary_in_real_session(tmp_path):
    executable = os.environ.get('CLOTH_NEXT_PPF_EXECUTABLE')
    if not executable:
        pytest.skip('CLOTH_NEXT_PPF_EXECUTABLE is not configured')
    report = run(Path(executable), tmp_path)
    assert report['tables']['bend'] == [10.0, 100.0]
    assert report.get('built_native_session') or report['frames'] == 1
