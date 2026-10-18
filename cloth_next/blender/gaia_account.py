# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Cached GAIA access state for Blender Preferences (filled by the account worker)."""

from __future__ import annotations

_state = {"access": None}


def access_granted() -> bool:
    return _state["access"] == "granted"
