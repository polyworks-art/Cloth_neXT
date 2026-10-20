# SPDX-License-Identifier: GPL-3.0-or-later
"""Vector glyphs for the modal editor toolbars.

Each icon is a tuple of strokes (polylines) and dots in a unit box from -1 to
1, drawn at any pixel size so the column matches Blender's navigation
buttons. Text glyphs (letters, numbers) are drawn by the caller.
"""
from __future__ import annotations

import math


def _arc(cx, cy, radius, start, end, steps=10):
    return tuple((cx + radius * math.cos(math.radians(start + (end - start) * i / steps)),
                  cy + radius * math.sin(math.radians(start + (end - start) * i / steps)))
                 for i in range(steps + 1))


def _arrow_head(tip, direction, size=.32):
    x, y = tip
    dx, dy = direction
    norm = math.hypot(dx, dy) or 1.0
    dx, dy = dx / norm, dy / norm
    return ((x - dx * size + dy * size * .8, y - dy * size - dx * size * .8), (x, y),
            (x - dx * size - dy * size * .8, y - dy * size + dx * size * .8))


ICONS = {
    "segment": {"strokes": (((-.62, -.48), (.62, .48)),),
                "dots": ((-.62, -.48, .2), (.62, .48, .2))},
    "free": {"strokes": (((-.7, -.55), (-.22, .38), (.24, -.22), (.7, .55)),),
             "dots": ((-.7, -.55, .15), (-.22, .38, .15), (.24, -.22, .15), (.7, .55, .15))},
    "multi": {"strokes": (((-.72, 0), (.05, 0)), ((.05, 0), (.72, .55)), ((.05, 0), (.72, -.55))),
              "dots": ((-.72, 0, .17), (.72, .55, .15), (.72, -.55, .15))},
    "edit": {"strokes": (((-.62, -.66), (-.5, -.12), (.42, .8), (.8, .42), (-.12, -.5),
                          (-.62, -.66)), ((.24, .62), (.62, .24)))},
    "reverse": {"strokes": (((-.7, .32), (.66, .32)), _arrow_head((.7, .32), (1, 0)),
                            ((.7, -.32), (-.66, -.32)), _arrow_head((-.7, -.32), (-1, 0)))},
    "back": {"strokes": (((.68, .45), (.68, -.1), (-.6, -.1)), _arrow_head((-.64, -.1), (-1, 0)))},
    "cancel": {"strokes": (_arc(0, 0, .62, 110, 430 - 50), _arrow_head(
        (.62 * math.cos(math.radians(380)), .62 * math.sin(math.radians(380))), (-.34, .94)))},
    "confirm": {"strokes": (((-.62, .02), (-.16, -.46), (.66, .46)),)},
    "close": {"strokes": (((-.5, -.5), (.5, .5)), ((-.5, .5), (.5, -.5)))},
    "delete": {"strokes": (((-.66, .5), (.66, .5)), ((-.2, .5), (-.2, .72), (.2, .72), (.2, .5)),
                           ((-.48, .5), (-.38, -.72), (.38, -.72), (.48, .5)),
                           ((-.12, .28), (-.08, -.5)), ((.12, .28), (.08, -.5)))},
    "threads": {"strokes": (((-.75, .62), (.75, .62)), ((-.75, -.62), (.75, -.62)),
                            ((-.5, .62), (-.3, -.62)), ((-.08, .62), (.12, -.62)),
                            ((.34, .62), (.54, -.62)))},
    "xray": {"strokes": (((-.72, -.25), (.22, -.25), (.22, .69), (-.72, .69), (-.72, -.25)),
                         ((-.22, -.69), (.72, -.69), (.72, .25), (-.22, .25), (-.22, -.69)))},
    "enable": {"strokes": (_arc(0, -.06, .62, 125, 415),
                           ((0, .2), (0, .74)))},
    "minus": {"strokes": (((-.5, 0), (.5, 0)),)},
    "plus": {"strokes": (((-.5, 0), (.5, 0)), ((0, -.5), (0, .5)))},
    "attach": {"strokes": (((-.62, -.62), (-.1, -.1)), ((.1, .1), (.62, .62))),
               "dots": ((-.62, -.62, .18), (.62, .62, .18), (0, 0, .16))},
    "smart": {"strokes": (((-.55, .7), (-.55, -.05)) + _arc(0, -.05, .55, 180, 360)[1:]
                          + ((.55, .7),), ((-.78, .7), (-.32, .7)), ((.32, .7), (.78, .7)))},
    "path": {"strokes": (((-.7, -.4), (-.25, .3), (.25, -.3), (.7, .4)),),
             "dots": ((-.7, -.4, .17), (.7, .4, .17))},
    "clear": {"strokes": (_arc(0, 0, .6, 0, 360, 18), ((-.42, -.42), (.42, .42)))},
    "next": {"strokes": (((-.62, 0), (.6, 0)), _arrow_head((.64, 0), (1, 0)))},
}


def icon(name):
    """Strokes and dots of ``name``; unknown names fall back to a dot."""
    entry = ICONS.get(name)
    if entry is None:
        return {"strokes": (), "dots": ((0, 0, .25),)}
    return {"strokes": tuple(entry.get("strokes", ())), "dots": tuple(entry.get("dots", ()))}
