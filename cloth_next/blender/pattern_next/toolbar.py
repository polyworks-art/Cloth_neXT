# SPDX-License-Identifier: GPL-3.0-or-later
"""The PATTERN NeXt top toolbar, drawn inside the 2D Pattern editor.

Original vector glyphs (unit box -1..1) and a compact horizontal bar. The
modal editor hit-tests exactly the rectangles painted here. Simulation is
deliberately absent until the AETHER fitting milestone; Arrangement lives in
the 3D view's "PATTERN NeXt" sidebar where it actually operates.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from ..tool_overlay import header_overlap

GLYPHS = {
    "SELECT": (((-.55, .75), (-.55, -.55), (-.2, -.2), (.05, -.75), (.25, -.65), (0, -.12),
                (.45, -.12), (-.55, .75)),),
    "POLYGON": (((-.75, -.55), (-.1, .75), (.75, .2), (.45, -.7), (-.75, -.55)),),
    "RECTANGLE": (((-.75, -.55), (.75, -.55), (.75, .55), (-.75, .55), (-.75, -.55)),),
    "ELLIPSE": (tuple((.8 * math.cos(math.radians(a)), .55 * math.sin(math.radians(a)))
                      for a in range(0, 361, 20)),),
    "EDIT": (((-.75, -.55), (-.1, .75), (.75, .2), (.45, -.7), (-.75, -.55)),
             ((-.2, .85), (0, .85), (0, .65), (-.2, .65), (-.2, .85))),
    "CURVE": (tuple((-.8 + 1.6 * t, -.5 + 1.6 * t * (1 - t) * 1.6)
                    for t in (i / 12 for i in range(13))),
              ((-.8, -.5), (-.35, .55)), ((.8, -.5), (.35, .55))),
    "INTERNAL": (((-.8, -.65), (.8, -.65), (.8, .65), (-.8, .65), (-.8, -.65)),
                 ((-.35, -.25), (.35, -.25), (.35, .25), (-.35, .25), (-.35, -.25))),
    "SEWING": (((-.8, -.6), (-.8, .6)), ((.8, -.6), (.8, .6)),
               ((-.8, -.45), (.8, -.15)), ((-.8, -.05), (.8, .25)), ((-.8, .35), (.8, .6))),
    "grid": (((-.75, -.75), (.75, -.75), (.75, .75), (-.75, .75), (-.75, -.75)),
             ((-.25, -.75), (-.25, .75)), ((.25, -.75), (.25, .75)),
             ((-.75, -.25), (.75, -.25)), ((-.75, .25), (.75, .25))),
    "points": (((-.7, -.6), (.7, .6)), ((-.3, -.7), (-.7, -.7), (-.7, -.3), (-.3, -.3),
                                         (-.3, -.7)), ((.3, .3), (.7, .3), (.7, .7), (.3, .7),
                                                       (.3, .3))),
    "edges": (((-.8, -.6), (.8, .6)), ((-.05, .35), (.35, -.05))),
    "mesh": (((-.8, -.6), (.8, -.6), (0, .7), (-.8, -.6)), ((-.4, .05), (.4, .05), (0, -.6),
                                                           (-.4, .05))),
    "lengths": (((-.8, -.2), (.8, -.2)), ((-.8, -.45), (-.8, .05)), ((.8, -.45), (.8, .05)),
                ((-.3, .35), (.3, .35))),
    "seams": (((-.8, -.6), (-.8, .6)), ((.8, -.6), (.8, .6)), ((-.8, 0), (.8, 0))),
    "frame": (((-.8, -.35), (-.8, -.75), (-.4, -.75)), ((.4, -.75), (.8, -.75), (.8, -.35)),
              ((.8, .35), (.8, .75), (.4, .75)), ((-.4, .75), (-.8, .75), (-.8, .35))),
}

TOOL_ROWS = (("SELECT", "Select"), ("POLYGON", "Polygon"), ("RECTANGLE", "Rectangle"),
             ("ELLIPSE", "Ellipse"), None, ("EDIT", "Edit Pattern"), ("CURVE", "Edit Curve"),
             ("INTERNAL", "Internal"), None, ("SEWING", "Sewing"))
TOOL_ROWS_FLAT = tuple(row[0] for row in TOOL_ROWS if row is not None)
TOGGLE_ROWS = (("snap:grid", "grid", "Grid snapping"), ("snap:vertex", "points", "Point snapping"),
               ("snap:edge", "edges", "Edge snapping"), None,
               ("show:mesh", "mesh", "Show triangulation"),
               ("show:lengths", "lengths", "Show lengths"), ("show:seams", "seams", "Show seams"),
               None, ("frame", "frame", "Frame all patterns (Home)"))


@dataclass(frozen=True)
class Button:
    identifier: str
    label: str
    glyph: str
    active: bool
    x: float
    y: float
    w: float
    h: float
    tooltip: str = ""

    def contains(self, px, py):
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


def _scale(context):
    from .draw import ui_scale
    return ui_scale(context)


def layout(context, region):
    """Buttons in region pixels: tools from the left, toggles from the right."""
    import blf
    scale = _scale(context)
    height = 26 * scale
    top = region.height - header_overlap(context) - 8 * scale
    y = top - height
    ui = context.window_manager.cloth_next_pattern_ui
    settings = context.scene.cloth_next_pattern_settings
    blf.size(0, round(11 * scale))
    gap, sep = 3 * scale, 10 * scale
    tools = [row for row in TOOL_ROWS if row is not None]
    toggles = [row for row in TOGGLE_ROWS if row is not None]
    labelled_width = (sum(height + blf.dimensions(0, label)[0] + 8 * scale + gap
                          for _key, label in tools) + 2 * sep
                      + len(toggles) * (height + gap) + 3 * sep + 40 * scale)
    labelled = labelled_width < region.width
    buttons, x = [], 10 * scale
    for row in TOOL_ROWS:
        if row is None:
            x += sep
            continue
        key, label = row
        width = height + (blf.dimensions(0, label)[0] + 8 * scale if labelled else 0)
        buttons.append(Button("tool:" + key, label, key, ui.tool == key, x, y, width, height, label))
        x += width + gap
    left_end = x
    x = region.width - 10 * scale
    states = {"snap:grid": settings.snap_grid, "snap:vertex": settings.snap_vertex,
              "snap:edge": settings.snap_edge, "show:mesh": settings.show_mesh,
              "show:lengths": settings.show_lengths, "show:seams": settings.show_seams,
              "frame": False}
    for row in reversed(TOGGLE_ROWS):
        if row is None:
            x -= sep
            continue
        key, glyph, tip = row
        x -= height
        if x < left_end + 20 * scale:
            break
        buttons.append(Button(key, "", glyph, states[key], x, y, height, height, tip))
        x -= gap
    return buttons


def hit(context, region, px, py):
    for button in layout(context, region):
        if button.contains(px, py):
            return button
    return None


def _glyph(name, cx, cy, size, color, region):
    from .draw import px_lines
    pairs = []
    for stroke in GLYPHS.get(name, ()):
        for a, b in zip(stroke, stroke[1:]):
            pairs += [(cx + a[0] * size, cy + a[1] * size), (cx + b[0] * size, cy + b[1] * size)]
    if pairs:
        px_lines(pairs, color, 1.5, region=region)


def draw(context, region, hover, _message=None):
    from .draw import TEXT, px_rounded, text
    buttons = layout(context, region)
    if not buttons:
        return
    scale = _scale(context)
    left = [b for b in buttons if b.identifier.startswith("tool:")]
    if left:
        x0, x1 = left[0].x - 4 * scale, left[-1].x + left[-1].w + 4 * scale
        px_rounded(x0, left[0].y - 4 * scale, x1 - x0, left[0].h + 8 * scale, 8 * scale,
                   (0.09, 0.10, 0.115, 0.92))
    right = [b for b in buttons if not b.identifier.startswith("tool:")]
    if right:
        x0, x1 = min(b.x for b in right) - 4 * scale, max(b.x + b.w for b in right) + 4 * scale
        px_rounded(x0, right[0].y - 4 * scale, x1 - x0, right[0].h + 8 * scale, 8 * scale,
                   (0.09, 0.10, 0.115, 0.92))
    for b in buttons:
        if b.active:
            fill = (0.20, 0.55, 0.85, 0.95) if b.identifier.startswith("tool:") else (0.20, 0.40, 0.55, 0.9)
        elif hover == b.identifier:
            fill = (1.0, 1.0, 1.0, 0.10)
        else:
            fill = None
        if fill:
            px_rounded(b.x, b.y, b.w, b.h, 5 * scale, fill)
        color = (1, 1, 1, 1) if b.active else (0.85, 0.88, 0.92, 0.95)
        _glyph(b.glyph, b.x + b.h / 2, b.y + b.h / 2, b.h * 0.3, color, region)
        if b.label and b.w > b.h:
            text(b.label, b.x + b.h - 2 * scale, b.y + b.h / 2 - 4 * scale, color, 11, shadow=False)
    if hover:
        tip = next((b for b in buttons if b.identifier == hover), None)
        if tip is not None and tip.tooltip and tip.w <= tip.h + 1:
            text(tip.tooltip, tip.x + tip.w, tip.y - 18 * scale, TEXT, 10, align="RIGHT")
