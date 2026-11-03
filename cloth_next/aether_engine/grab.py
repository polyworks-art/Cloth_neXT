# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live grab: a hard grab that follows the mouse until the cloth around it would overstretch.

Marvelous (black-box, NEON): the grabbed point sticks exactly to the mouse; pulled very far it simply
stops (10-15 cm from the body) while the mouse keeps moving, and after letting go the cloth is at rest
at once. A soft grab spring instead lagged up to 10 cm behind the mouse under a Stretch Limit, and a
hard grab without a limit stretched the cloth around it 5-18x (NEON vest), which then recovered slowly.
"""
from __future__ import annotations

import time

import numpy as np

# Largest edge stretch of the grabbed cloth (relative to its length when grabbed) before the grab stops
# following. Measured with the whole cloth watched (RINGS 0); NEON vest pulled 15 cm off the body:
#   cap 1.3 / 1.6: the point sticks to the target (0 mm), 1.06-1.5x max stretch 2 s after letting go,
#   0-2 overstretched edges; a 2-ring watch at 1.3 missed the soft Inlay zone (3.7x, 20 edges kept).
#   Ball sheet lifted by one point: 0.31 m (1.3) / 0.49 m (1.6) before it stops, no cap 2.6 m (rubber).
# CLOTH_NEXT_GRAB_CAP / CLOTH_NEXT_GRAB_RINGS override them for tests.
STRETCH_CAP = float(__import__("os").environ.get("CLOTH_NEXT_GRAB_CAP", "1.6"))
RINGS = int(__import__("os").environ.get("CLOTH_NEXT_GRAB_RINGS", "0"))   # rings watched (0 = whole cloth)
# Between the cap and HARD_CAP the grab keeps creeping toward the mouse by CREEP per call (30 Hz: 0.15 m/s),
# so a heavy cloth that needs more stretch to come along still follows, slowly; beyond HARD_CAP it stops.
HARD_CAP = float(__import__("os").environ.get("CLOTH_NEXT_GRAB_HARD_CAP", "2.0"))
CREEP = float(__import__("os").environ.get("CLOTH_NEXT_GRAB_CREEP", "0.005"))   # m
# Only an overstretch that lasts brakes the grab: during a fast drag the cloth lags behind for a moment
# and its stretch spikes past the cap. Braking on those spikes made fast pulls stiff and slow pulls
# fluid (Tim: "like a non-Newtonian fluid"). Past HARD_CAP it brakes at once (without that, a fast lift
# stretched the ball sheet to 1.8 m like rubber, and it fell back slowly).
SUSTAIN = float(__import__("os").environ.get("CLOTH_NEXT_GRAB_SUSTAIN", "0.4"))   # s
# The grab never leads the cloth by more than this: no edge from a grabbed vertex to its neighbours gets
# longer than LEAD x its length. A fast drag (40 cm in 0.3 s) jumped 4 cm per tick ahead of the cloth,
# stretched it 11x before the cap above could react, and left it tangled (edges at 2.4-4x for good).
# NEON stress test (8 drags of 40 cm in 0.3 s, away from and along the body): lead 2.0 peaks at 1.5-2.2x
# and ends at rest (1.04-1.07x, no edge over 2x, nothing frozen); 1.5 lifts a heavy sheet less, 2.5
# left points frozen and edges at 1.47x.
LEAD = float(__import__("os").environ.get("CLOTH_NEXT_GRAB_LEAD", "2.0"))


class GrabCap:
    """Watches the cloth's edges (or those near the grab) and decides whether the grab target may move on."""

    def __init__(self, triangles: np.ndarray, rest: np.ndarray, vertices, start: np.ndarray | None = None,
                 cap: float = STRETCH_CAP, rings: int = RINGS):
        """``start``: positions when grabbed. An edge already longer than its rest length then (cloth
        that hangs stretched) is measured from that length, so the grab can always move at first."""
        tris = np.asarray(triangles, np.int64).reshape(-1, 3)
        edges = np.unique(np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), 1), axis=0)
        near = np.zeros(len(rest), bool)
        near[np.asarray(vertices, np.int64)] = True
        for _ in range(rings):
            hit = near[edges[:, 0]] | near[edges[:, 1]]
            near[edges[hit].ravel()] = True
        if rings <= 0:
            near[:] = True
        self.edges = edges[near[edges[:, 0]] & near[edges[:, 1]]]
        near[np.asarray(vertices, np.int64)] = False
        self.ring = np.nonzero(near)[0]   # the cloth the grab pulls against
        r = np.asarray(rest, np.float64)
        self.rest = np.maximum(np.linalg.norm(r[self.edges[:, 0]] - r[self.edges[:, 1]], axis=1), 1e-12)
        if start is not None:
            x = np.asarray(start, np.float64)
            self.rest = np.maximum(self.rest, np.linalg.norm(x[self.edges[:, 0]] - x[self.edges[:, 1]], axis=1))
        self.cap = float(cap)
        self.target = None
        self.over_since = None
        g = np.zeros(len(rest), bool)
        g[np.asarray(vertices, np.int64)] = True
        index = {int(v): k for k, v in enumerate(np.asarray(vertices, np.int64).tolist())}
        lead = edges[g[edges[:, 0]] ^ g[edges[:, 1]]]
        self.lead_g = np.array([index[int(a)] if g[a] else index[int(b)] for a, b in lead], np.int64)
        self.lead_n = np.array([b if g[a] else a for a, b in lead], np.int64)
        L = np.linalg.norm(r[lead[:, 0]] - r[lead[:, 1]], axis=1) if len(lead) else np.zeros(0)
        if start is not None and len(lead):
            L = np.maximum(L, np.linalg.norm(x[lead[:, 0]] - x[lead[:, 1]], axis=1))
        self.lead_len = np.maximum(L, 1e-12)

    def stretch(self, positions: np.ndarray) -> float:
        if not len(self.edges):
            return 1.0
        x = np.asarray(positions, np.float64)
        return float((np.linalg.norm(x[self.edges[:, 0]] - x[self.edges[:, 1]], axis=1) / self.rest).max())

    def _lead(self, target: np.ndarray, positions: np.ndarray, grabbed) -> np.ndarray:
        """``target`` moved back toward the grabbed points as far as needed to keep the lead edges short."""
        if not len(self.lead_n):
            return target
        x = np.asarray(positions, np.float64)
        cur = x[np.asarray(grabbed, np.int64)]
        nb = x[self.lead_n]

        def worst(s):
            p = cur[self.lead_g] + (target[self.lead_g] - cur[self.lead_g]) * s
            return float((np.linalg.norm(p - nb, axis=1) / self.lead_len).max())

        if worst(1.0) <= LEAD or worst(1.0) <= worst(0.0):
            return target          # within the lead, or easing an already long edge
        if worst(0.0) > LEAD:
            return cur             # already as far as the cloth allows: stay
        lo, hi = 0.0, 1.0
        for _ in range(12):
            mid = 0.5 * (lo + hi)
            lo, hi = (mid, hi) if worst(mid) <= LEAD else (lo, mid)
        return cur + (target - cur) * lo

    def follow(self, proposed: np.ndarray, positions: np.ndarray, grabbed, now: float | None = None) -> np.ndarray:
        """The grab target for this mouse position: the mouse while the cloth allows it, else where it stopped.

        Past the cap the target only moves where it does not pull further away from the cloth around
        it (back toward it, or along it), so the artist can always ease off."""
        proposed = np.asarray(proposed, np.float64)
        stretch = self.stretch(positions)
        now = time.monotonic() if now is None else now
        if stretch <= self.cap:
            self.over_since = None
        elif self.over_since is None:
            self.over_since = now
        hard = self.cap * HARD_CAP / STRETCH_CAP
        if (self.target is None or self.over_since is None or not len(self.ring)
                or (now - self.over_since < SUSTAIN and stretch <= hard)):
            self.target = self._lead(proposed, positions, grabbed)
            return self.target
        anchor = np.asarray(positions, np.float64)[self.ring].mean(0)
        if np.linalg.norm(proposed - anchor, axis=1).mean() <= np.linalg.norm(self.target - anchor, axis=1).mean():
            self.target = proposed
        elif stretch <= self.cap * HARD_CAP / STRETCH_CAP:
            step = proposed - self.target
            dist = float(np.linalg.norm(step, axis=1).max())
            if dist > CREEP:
                step *= CREEP / dist
            self.target = self.target + step
        self.target = self._lead(self.target, positions, grabbed)
        return self.target
