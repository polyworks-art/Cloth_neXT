# SPDX-License-Identifier: GPL-3.0-or-later
"""Artist-facing friction calibration for the PPF solver."""

from __future__ import annotations


PPF_FRICTION_SCALE = 0.5


def artist_friction_to_ppf(value: float) -> float:
    """Map the unchanged 0..1 UI range onto the calibrated PPF range."""
    return float(value) * PPF_FRICTION_SCALE
