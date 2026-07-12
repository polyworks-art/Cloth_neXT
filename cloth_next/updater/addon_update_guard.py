# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Guards that keep add-on and solver updates out of active solver work.

Cloth NeXt updates install through Blender's own extension mechanism; solver
updates install through the separate managed installer. Neither may start
while the solver is starting or running, a transfer, build, simulation, frame
fetch, or cache write is active, or a cancellation is in progress.
"""

from __future__ import annotations

from ..core.state import ApplicationState

UPDATE_SAFE_STATES = frozenset({
    ApplicationState.NOT_INSTALLED,
    ApplicationState.STOPPED,
    ApplicationState.ERROR,
})

UPDATE_BLOCKING_STATES = frozenset(ApplicationState) - UPDATE_SAFE_STATES

ADDON_UPDATE_PREPARATION = (
    "Finish or cancel the active solve.",
    "Stop the solver process Cloth NeXt started (never an external server).",
    "Confirm the process has exited.",
    "Close remaining file and socket handles.",
    "Apply the update through Blender's extension update mechanism.",
    "Restart Blender if the extension system requires it.",
)

SOLVER_UPDATE_PREPARATION = (
    "Finish or cancel the active solve.",
    "Stop the solver process Cloth NeXt started (never an external server).",
    "Confirm the process has exited.",
    "Download and hash-verify the manifest-pinned new version.",
    "Install it side by side; probe version, protocol, and schema.",
    "Run the real health check.",
    "Switch the active version only after the health check passed.",
)


def can_start_addon_update(state: ApplicationState) -> bool:
    return state in UPDATE_SAFE_STATES


def can_start_solver_update(state: ApplicationState) -> bool:
    return state in UPDATE_SAFE_STATES
