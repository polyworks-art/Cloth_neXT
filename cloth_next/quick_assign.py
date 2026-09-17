# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent marking-menu geometry and gesture state."""
from dataclasses import dataclass, replace
import math


ROLE_ORDER = ("CLOTH", "ROD", "RIGID_BODY", "SOFT_BODY", "COLLIDER")


@dataclass(frozen=True)
class RadialLayout:
    center: tuple[float, float]
    roles: tuple[str, ...]
    rotation: float
    scale: float
    width: float
    height: float

    @property
    def deadzone(self):
        return 25 * self.scale

    @property
    def inner_radius(self):
        return 30 * self.scale

    @property
    def outer_radius(self):
        return 112 * self.scale

    @property
    def bubble_radius(self):
        return 18 * self.scale

    def angle(self, index):
        return self.rotation + (index + .5) * math.pi / len(self.roles)

    def label_angle(self, index):
        """Keep captions upright, including the two left-hand role sectors."""
        angle = (self.angle(index) + math.pi) % math.tau - math.pi
        if angle > math.pi / 2:
            angle -= math.pi
        elif angle < -math.pi / 2:
            angle += math.pi
        return angle

    def point(self, index, radius=None):
        radius = 84.69175 * self.scale if radius is None else radius
        a = self.angle(index)
        return (self.center[0] + radius * math.cos(a),
                self.center[1] + radius * math.sin(a))

    def sector_mesh(self, index, steps=20):
        """Presentation wedge uses the same angles as directional hit testing.

        Extend beneath the neutral center button and fade toward the outer
        limit. Radius/alpha pairs keep the label legible below the role bubble.
        """
        start = self.rotation + index * math.pi / len(self.roles)
        span = math.pi / len(self.roles)
        rings = ((17*self.scale, .82), (45*self.scale, .58),
                 (self.outer_radius, 0.))
        vertices, opacity, triangles = [], [], []
        for radius, alpha in rings:
            for step in range(steps+1):
                angle = start + span*step/steps
                vertices.append((self.center[0]+radius*math.cos(angle),
                                 self.center[1]+radius*math.sin(angle)))
                opacity.append(alpha)
        for ring in range(len(rings)-1):
            for step in range(steps):
                a = ring*(steps+1)+step
                b = a+steps+1
                triangles.extend(((a, b, a+1), (a+1, b, b+1)))
        return vertices, opacity, triangles

    def hit(self, point):
        x, y = point
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        dx, dy = x - self.center[0], y - self.center[1]
        radius = math.hypot(dx, dy)
        if not self.inner_radius <= radius <= self.outer_radius:
            return None
        angle = (math.atan2(dy, dx) - self.rotation) % math.tau
        if angle >= math.pi or not self.roles:
            return None
        return self.roles[min(int(angle / math.pi * len(self.roles)),
                              len(self.roles) - 1)]


def make_layout(center, width, height, scale=1., roles=ROLE_ORDER):
    """Prefer an upward fan; rotate the entire fan in quarter turns at edges.

    Never reorder roles or move the gesture origin. Pick the orientation with
    the least bubble overflow, with stable tie-breaking in favor of up.
    """
    candidates = [RadialLayout(center, tuple(roles), rotation, scale, width, height)
                  for rotation in (0., math.pi, -math.pi / 2, math.pi / 2)]

    def overflow(layout):
        margin = 24 * scale
        return sum(max(0, margin-x) + max(0, x+margin-width)
                   + max(0, margin-y) + max(0, y+margin-height)
                   for x, y in (layout.point(i) for i in range(len(roles))))

    layout = min(candidates, key=overflow)
    if overflow(layout):
        # Very small viewports may not fit any full-size orientation. Keep
        # origin/angles fixed and shrink presentation and hit regions together.
        cx, cy = center
        factors = [1.]
        for i in range(len(roles)):
            dx = 84.69175*math.cos(layout.angle(i))
            dy = 84.69175*math.sin(layout.angle(i))
            for space, extent in ((cx, 24-dx), (width-cx, 24+dx),
                                  (cy, 24-dy), (height-cy, 24+dy)):
                if extent > 0:
                    factors.append(max(0., space) / (extent*scale))
        layout = replace(layout, scale=scale*max(.01, min(factors)))
    return layout


class Gesture:
    """A release returns a role once; cancellation can never return a role."""
    def __init__(self, layout, now):
        self.layout = layout
        self.started = now
        self.state = "PRESSED"
        self.target = None
        self.position = layout.center
        self.opened = None

    def update(self, position, now, valid_roles=ROLE_ORDER):
        if self.state in {"IDLE", "CANCEL", "COMMIT"}:
            return
        self.position = position
        distance = math.dist(position, self.layout.center)
        if self.state == "PRESSED":
            if now - self.started < .18 and distance < self.layout.deadzone:
                return
            self.opened = now
        target = self.layout.hit(position)
        self.target = target if target in valid_roles else None
        self.state = "TARGET_HOVER" if self.target else "RADIAL_OPEN"

    def release(self, position, now, valid_roles=ROLE_ORDER):
        self.update(position, now, valid_roles)
        role = self.target
        self.state = "COMMIT" if role else "CANCEL"
        self.target = None
        return role

    def cancel(self):
        self.target = None
        self.state = "CANCEL"
