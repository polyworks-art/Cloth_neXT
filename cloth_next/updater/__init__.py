# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Separate lifecycles: Blender-native add-on updates, separate solver installer.

Importing this package must never trigger network access, downloads, or writes.
Every download of the external PPF Contact Solver (ST Tech / ZOZO) requires an
explicit user confirmation first; see docs/RELEASE_POLICY.md sections 6 and 13.
"""
