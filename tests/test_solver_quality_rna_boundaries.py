import numpy as np
import pytest
from cloth_next.solver_quality import SolverQualitySettings,SolverQualityValidationError,MIN_CG_TOL,MAX_CG_TOL

@pytest.mark.parametrize('boundary',[MIN_CG_TOL,MAX_CG_TOL])
def test_accepts_real_rna_float32_tolerance_boundary(boundary):
    value=float(np.float32(boundary))
    assert SolverQualitySettings(cg_tol=value).cg_tol==value

@pytest.mark.parametrize('boundary,direction',[(MIN_CG_TOL,0.),(MAX_CG_TOL,float('inf'))])
def test_rejects_next_float32_outside_tolerance_boundary(boundary,direction):
    value=float(np.nextafter(np.float32(boundary),np.float32(direction),dtype=np.float32))
    with pytest.raises(SolverQualityValidationError):SolverQualitySettings(cg_tol=value)
