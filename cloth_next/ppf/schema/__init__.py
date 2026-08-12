# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exact payload encoders for the supported PPF compatibility profiles.

The original schema subset was verified against upstream commit ``7193f158``
(``crates/ppf-cts-formats/src/envelope.rs``, ``kinds/scene.rs``,
``kinds/param.rs``, ``blender_addon/core/encoder``) and the decoder shipped
with the locally installed official solver release
(``frontend/_cbor_bridge_.py``, ``frontend/_decoder_.py``). The official
Blender add-on is never imported. Protocol-specific parameter differences are
selected explicitly by the compatibility profile.
"""
