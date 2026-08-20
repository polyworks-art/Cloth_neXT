# SPDX-License-Identifier: GPL-3.0-or-later
"""Compatibility exports for the authoritative VEYRA repair solver.

New code belongs in :mod:`cloth_next.veyra.solver`; this module deliberately
contains no repair mathematics so older imports keep working without two
implementations drifting apart.
"""

from .veyra.solver import *  # noqa: F401,F403
