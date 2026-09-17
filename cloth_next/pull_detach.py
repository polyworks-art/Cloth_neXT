# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure pull geometry and hysteresis; no Blender or persistent state."""
from dataclasses import dataclass


@dataclass
class PullGesture:
    start_x: float
    distance: float
    progress: float = 0.0
    state: str = "PULLING"

    def update(self, x):
        if self.state == "IDLE":
            return
        self.progress = max(0.0, min(1.0, (self.start_x - x) / self.distance))
        if self.progress >= .95:
            self.state = "ARMED"
        elif self.progress <= .80:
            self.state = "PULLING"

    def release(self, x):
        self.update(x)
        armed = self.state == "ARMED"
        self.cancel()
        return armed

    def cancel(self):
        self.state = "IDLE"
        self.progress = 0.0


def logo_hit(bounds, x, y):
    if bounds is None:
        return False
    bx, by, _, h, s = bounds
    return bx <= x <= bx + 54*s and by <= y <= by+h
