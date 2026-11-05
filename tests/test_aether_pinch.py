# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinch report: frames where an animated collider touches itself (reported, not counted as errors)."""
import numpy as np

from cloth_next.aether_engine import pinch


def _sheet(n=21, size=0.2, z=0.0, x0=0.0):
    g = np.linspace(0.0, size, n)
    X, Y = np.meshgrid(g, g)
    v = np.stack([X.ravel() + x0, Y.ravel(), np.full(n * n, z)], 1)
    t = [[j * n + i, j * n + i + 1, j * n + i + n + 1] for j in range(n - 1) for i in range(n - 1)]
    t += [[j * n + i, j * n + i + n + 1, j * n + i + n] for j in range(n - 1) for i in range(n - 1)]
    return v, np.array(t)


def test_two_parts_of_one_collider_closing_in_are_reported():
    a, ta = _sheet(z=0.0)
    b, tb = _sheet(z=0.10)                    # a second body part 10 cm above, same object
    rest = np.concatenate([a, b])
    tris = np.concatenate([ta, tb + len(a)]).ravel()
    frames = []
    for k in range(10):                        # the upper part comes down; touches from frame 8 on
        x = rest.copy()
        x[len(a):, 2] = max(0.0, 0.10 - 0.0125 * (k + 1))
        frames.append(x)
    rep = pinch.self_contact("Body", rest, tris, np.array(frames))
    assert rep is not None and rep.frames == (7, 8, 9)
    assert pinch.ranges(rep.frames, 2) == "9-11"


def test_static_or_free_collider_is_not_reported():
    a, ta = _sheet()
    assert pinch.self_contact("Body", a, ta.ravel(), None) is None
    assert pinch.self_contact("Body", a, ta.ravel(), np.array([a + [0.0, 0.0, 0.01 * k] for k in range(5)])) is None
