# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exact PPF 0.11 schema-1 payload encoders (independent reproduction).

Verified against the pinned upstream sources at commit ``7193f158``
(``crates/ppf-cts-formats/src/envelope.rs``, ``kinds/scene.rs``,
``kinds/param.rs``, ``blender_addon/core/encoder``) and the decoder shipped
with the locally installed official solver release
(``frontend/_cbor_bridge_.py``, ``frontend/_decoder_.py``). The official
Blender add-on is never imported.
"""
