"""Live grab: a hard grab that follows the mouse until the cloth around it would overstretch.

Marvelous (black-box, NEON): the grabbed point sticks exactly to the mouse; pulled very far it simply
stops (10-15 cm from the body) while the mouse keeps moving, and after letting go the cloth is at rest
at once. A soft grab spring instead lagged up to 10 cm behind the mouse under a Stretch Limit, and a
hard grab without a limit stretched the cloth around it 5-18x (NEON vest), which then recovered slowly.
"""
from __future__ import annotations

import numpy as np

# Largest edge stretch of the grabbed cloth (relative to its length when grabbed) before the grab stops
# following. Measured with the whole cloth watched (RINGS 0); NEON vest pulled 15 cm off the body:
#   cap 1.3 / 1.6: the point sticks to the target (0 mm), 1.06-1.5x max stretch 2 s after letting go,
#   0-2 overstretched edges; a 2-ring watch at 1.3 missed the soft Inlay zone (3.7x, 20 edges kept).
#   Ball sheet lifted by one point: 0.31 m (1.3) / 0.49 m (1.6) before it stops, no cap 2.6 m (rubber).
# CLOTH_NEXT_GRAB_CAP / CLOTH_NEXT_GRAB_RINGS override them for tests.
STRETCH_CAP = float(__import__("os").environ.get("CLOTH_NEXT_GRAB_CAP", "1.6"))
RINGS = int(__import__("os").environ.get("CLOTH_NEXT_GRAB_RINGS", "0"))   # rings watched (0 = whole cloth)


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

    def stretch(self, positions: np.ndarray) -> float:
        if not len(self.edges):
            return 1.0
        x = np.asarray(positions, np.float64)
        return float((np.linalg.norm(x[self.edges[:, 0]] - x[self.edges[:, 1]], axis=1) / self.rest).max())

    def follow(self, proposed: np.ndarray, positions: np.ndarray, grabbed) -> np.ndarray:
        """The grab target for this mouse position: the mouse while the cloth allows it, else where it stopped.

        Past the cap the target only moves where it does not pull further away from the cloth around
        it (back toward it, or along it), so the artist can always ease off."""
        proposed = np.asarray(proposed, np.float64)
        if self.target is None or self.stretch(positions) <= self.cap or not len(self.ring):
            self.target = proposed
            return proposed
        anchor = np.asarray(positions, np.float64)[self.ring].mean(0)
        if np.linalg.norm(proposed - anchor, axis=1).mean() <= np.linalg.norm(self.target - anchor, axis=1).mean():
            self.target = proposed
        return self.target
