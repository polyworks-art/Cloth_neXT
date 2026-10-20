# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-independent layout and hit testing for modal viewport toolbars.

Sewing and Object Attachment editors draw a vertical button column in the
style of the Cloth NeXt simulation toolbar, directly below Blender's
navigation buttons on the right edge of the 3D View. The top of the viewport
stays free for other add-ons. The modal operator owns all input, so it
hit-tests exactly the rectangles that were painted.
"""
from __future__ import annotations

from dataclasses import dataclass

SEPARATOR = None

BUTTON = 24.0                     # diameter, matches Blender's navigation buttons
GAP = 5.0
SEPARATOR_HEIGHT = 11.0
MARGIN = 10.0


@dataclass(frozen=True)
class ToolButton:
    identifier: str
    label: str                    # tooltip title; also the text of text-only glyphs
    kind: str = "action"          # action, mode, toggle, primary, danger, value
    active: bool = False
    enabled: bool = True
    tooltip: str = ""
    icon: str = ""                # tool_icons name or PNG "tool_<icon>.png"; "" = text glyph


@dataclass(frozen=True)
class PlacedButton:
    button: ToolButton
    x: float
    y: float
    width: float
    height: float

    def contains(self, x, y):
        return (self.x <= x <= self.x + self.width
                and self.y <= y <= self.y + self.height)


@dataclass(frozen=True)
class ToolbarLayout:
    x: float
    y: float
    width: float
    height: float
    scale: float
    buttons: tuple[PlacedButton, ...]
    separators: tuple[float, ...]

    def contains(self, x, y):
        return (self.x <= x <= self.x + self.width
                and self.y <= y <= self.y + self.height)

    def button_at(self, x, y):
        for placed in self.buttons:
            if placed.contains(x, y):
                return placed
        return None

    def placed(self, identifier):
        return next((p for p in self.buttons if p.button.identifier == identifier), None)

    def hit(self, x, y):
        """Identifier of the enabled button under (x, y), else None."""
        placed = self.button_at(x, y)
        if placed is None or not placed.button.enabled or placed.button.kind == "value":
            return None
        return placed.button.identifier


def _flatten(items):
    """Buttons and separators in column order; grouped tuples stack too."""
    result, previous = [], SEPARATOR
    for item in items:
        if item is SEPARATOR:
            if previous is not SEPARATOR:
                result.append(SEPARATOR)
            previous = item
            continue
        for button in (item if isinstance(item, (tuple, list)) else (item,)):
            result.append(button)
            previous = button
    while result and result[-1] is SEPARATOR:
        result.pop()
    return result


def vertical_layout(items, center_x, top, bottom, scale=1.0):
    """Column of round buttons hanging from ``top``, centred on ``center_x``.

    When the column does not fit above ``bottom`` it shrinks uniformly, so
    painted and hit-tested rectangles never disagree.
    """
    rows = _flatten(items)

    def measure(factor):
        height, previous = 0.0, None
        for row in rows:
            if row is SEPARATOR:
                height += SEPARATOR_HEIGHT * factor
            else:
                if previous is not None and previous is not SEPARATOR:
                    height += GAP * factor
                height += BUTTON * factor
            previous = row
        return height

    factor = max(.1, float(scale))
    height = measure(factor)
    available = max(1.0, top - bottom)
    if height > available:
        factor *= max(.4, available / height)
        height = measure(factor)
    size = BUTTON * factor
    x0 = center_x - size / 2
    cursor = top
    placed, separators, previous = [], [], None
    for row in rows:
        if row is SEPARATOR:
            separators.append(cursor - SEPARATOR_HEIGHT * factor / 2)
            cursor -= SEPARATOR_HEIGHT * factor
            previous = row
            continue
        if previous is not None and previous is not SEPARATOR:
            cursor -= GAP * factor
        placed.append(PlacedButton(row, x0, cursor - size, size, size))
        cursor -= size
        previous = row
    return ToolbarLayout(x0, top - height, size, height, factor,
                         tuple(placed), tuple(separators))


def navigation_bottom(region_top, *, gizmo_size=80.0, ui_scale=1.0, show_navigation=True,
                      buttons=4):
    """Lowest pixel row used by Blender's navigation gizmo and its buttons.

    ``region_top`` is the top of the visible region (below overlapping
    headers). Mirrors the 3D View layout: the rotation gizmo, then one
    button (zoom, pan, camera, perspective) every 0.375 gizmo sizes.
    """
    if not show_navigation:
        return region_top - MARGIN * ui_scale
    size = gizmo_size * ui_scale
    last_center = size * (1.4 + .375 * (buttons - 1))
    return region_top - last_center - size * .15 - MARGIN * ui_scale


def navigation_center(region_right, *, gizmo_size=80.0, ui_scale=1.0):
    """Horizontal centre of the navigation button column."""
    return region_right - gizmo_size * ui_scale * .3
