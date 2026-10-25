# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""TETHYS dynamic wetness: Blender-side configuration, data exchange and playback.

The numerical surface-water solver is the bundled native TETHYS Engine.
This package only describes wetness profiles, prepares binary inputs,
launches the engine, validates its cache and maps results back to Blender.
It deliberately contains no transport, absorption or drying algorithm.
"""
