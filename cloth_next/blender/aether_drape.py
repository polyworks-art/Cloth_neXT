# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draped state of AETHER Cloth objects (Marvelous Designer style).

Stopping the Live Simulation keeps the cloth where the simulation left it: the
positions are stored as the shape key ``Cloth NeXt Drape`` (value 1) on the
original object, so the object shows the draped garment while its basis keeps
the pattern / rest shape. The next Live Simulation or Bake simulates from the
draped state with the basis as rest shape (the drape is muted while the rest
shape is captured), so a garment is draped once and refined from there.
``Clear Drape`` removes the key and returns to the rest shape.
"""
from __future__ import annotations

from contextlib import contextmanager

import numpy as np

DRAPE_KEY = "Cloth NeXt Drape"


def drape_block(obj):
    keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    if keys is None:
        return None
    return keys.key_blocks.get(DRAPE_KEY)


def has_drape(obj) -> bool:
    block = drape_block(obj)
    return block is not None and not block.mute and block.value > 0.0


def drape_local(obj) -> np.ndarray | None:
    """Draped positions in object space (V, 3) float32, or None."""
    block = drape_block(obj)
    if block is None or block.mute or not block.value > 0.0:
        return None
    out = np.empty(len(block.data) * 3, np.float32)
    block.data.foreach_get("co", out)
    return out.reshape(-1, 3)


@contextmanager
def drape_muted(objects):
    """Mute the drape keys while the rest shape is captured (restored afterwards)."""
    muted = []
    for obj in objects:
        block = drape_block(obj)
        if block is not None and not block.mute:
            block.mute = True
            muted.append(block)
    try:
        yield
    finally:
        for block in muted:
            try:
                block.mute = False
            except ReferenceError:
                pass


def store_drape(obj, local: np.ndarray) -> None:
    """Write positions (object space) as the drape key, creating the basis key if the mesh has none."""
    if obj.data.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    block = drape_block(obj)
    if block is None:
        block = obj.shape_key_add(name=DRAPE_KEY, from_mix=False)
    block.data.foreach_set("co", np.ascontiguousarray(local, np.float32).reshape(-1))
    block.value = 1.0
    block.mute = False
    obj.data.update()


def clear_drape(obj) -> bool:
    block = drape_block(obj)
    if block is None:
        return False
    obj.shape_key_remove(block)
    keys = obj.data.shape_keys
    if keys is not None and len(keys.key_blocks) == 1 and keys.key_blocks[0].name == "Basis":
        obj.shape_key_remove(keys.key_blocks[0])   # only the basis we created is left
    obj.data.update()
    return True
