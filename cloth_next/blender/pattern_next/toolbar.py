# SPDX-License-Identifier: GPL-3.0-or-later
"""The 2D Pattern editor's tool strip and status strip.

Both are docked to the editor (full width, flat, theme-coloured like a
Blender header) rather than floating. Tools are grouped Create / Edit /
Construct / Display, each a 26 px button with a monochrome Cloth NeXt-style
icon. One accent treatment marks the active tool (tinted plate plus a 2 px
underline); display toggles are neutral (bright icon when on, dim when
off). When the editor gets narrow the Display group folds into one button
that opens a native Blender popover, then the Frame button goes (Home
keeps working). The modal editor hit-tests exactly the rectangles drawn.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..tool_overlay import header_overlap, sidebar_overlap
from . import style
from .properties import TOOL_INFO

TOOL_ICONS = {key: icon for key, _label, _description, _shortcut, icon in TOOL_INFO}
TOOL_LABELS = {key: label for key, label, _description, _shortcut, _icon in TOOL_INFO}
TOOL_ROWS_FLAT = tuple(key for key, *_ in TOOL_INFO)

GROUPS = (
    ("Create", ("tool:POLYGON", "tool:RECTANGLE", "tool:ELLIPSE", "tool:CURVE_DRAW")),
    ("Edit", ("tool:SELECT", "tool:EDIT", "tool:CURVE", "tool:INTERNAL")),
    ("Construct", ("tool:SEWING", "action:mirror", "action:duplicate")),
    ("Display", ("toggle:show_grid", "toggle:use_snap", "toggle:show_lengths",
                 "toggle:show_mesh", "toggle:show_seams")),
)
ACTIONS = {
    "action:mirror": ("mirror", "Mirror", "Mirrored copy of the selection with linked symmetry",
                      ""),
    "action:duplicate": ("duplicate", "Duplicate", "Duplicate the selected patterns",
                         "Shift D"),
    "action:frame": ("frame", "Frame All", "Fit all patterns in the editor", "Home"),
    "toggle:show_inspector": ("inspector", "Inspector", "Pattern list and properties column",
                              "I"),
    "menu:display": ("grid", "Display", "Grid, snapping, measurements, triangulation and "
                     "sewing display", ""),
}
TOGGLES = {
    "toggle:show_grid": ("grid", "Grid", "Measurement grid"),
    "toggle:use_snap": ("snap", "Snap", "Snap to grid, points and edges (Ctrl suspends)"),
    "toggle:show_lengths": ("measure", "Measurements", "Edge lengths of the selection"),
    "toggle:show_mesh": ("mesh", "Triangulation", "Cloth triangulation of every pattern"),
    "toggle:show_seams": ("seams", "Sewing Display", "Sewing relationships"),
}


@dataclass(frozen=True)
class Button:
    identifier: str
    icon: str
    label: str
    tooltip: str
    shortcut: str
    kind: str          # tool, action, toggle, menu
    active: bool
    enabled: bool
    x: float
    y: float
    w: float
    h: float

    def contains(self, px, py):
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


@dataclass(frozen=True)
class Strip:
    x: float
    y: float
    w: float
    h: float
    buttons: tuple
    separators: tuple

    def contains(self, px, py):
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


def _button(context, identifier, has_selection):
    ui = context.window_manager.cloth_next_pattern_ui
    settings = context.scene.cloth_next_pattern_settings
    if identifier.startswith("tool:"):
        key = identifier[5:]
        info = next(row for row in TOOL_INFO if row[0] == key)
        return dict(icon=info[4], label=info[1], tooltip=info[2], shortcut=info[3], kind="tool",
                    active=ui.tool == key, enabled=True)
    if identifier in TOGGLES:
        icon, label, tooltip = TOGGLES[identifier]
        return dict(icon=icon, label=label, tooltip=tooltip, shortcut="", kind="toggle",
                    active=bool(getattr(settings, identifier[7:])), enabled=True)
    if identifier == "toggle:show_inspector":
        icon, label, tooltip, shortcut = ACTIONS[identifier]
        return dict(icon=icon, label=label, tooltip=tooltip, shortcut=shortcut, kind="toggle",
                    active=bool(settings.show_inspector), enabled=True)
    icon, label, tooltip, shortcut = ACTIONS[identifier]
    enabled = has_selection if identifier in {"action:mirror", "action:duplicate"} else True
    return dict(icon=icon, label=label, tooltip=tooltip, shortcut=shortcut,
                kind="menu" if identifier.startswith("menu:") else "action",
                active=False, enabled=enabled)


def layout(context, region, has_selection=True):
    """Tool strip in region pixels (top edge, below any overlapping header).

    Groups never split: when the editor is too narrow for one row, the
    Display group folds into one popover button and the remaining groups
    wrap onto further rows, so every tool stays reachable at any width.
    """
    s = style.ui_scale(context)
    bar, size, gap, group_gap, margin = style.BAR * s, style.BUTTON * s, 2 * s, 11 * s, 6 * s
    top = region.height - header_overlap(context)
    right = region.width - sidebar_overlap(context)
    from . import dock
    docked = dock.rect(context, region)
    if docked is not None:
        right = docked[0]

    def group_width(ids):
        return len(ids) * size + (len(ids) - 1) * gap

    groups = list(GROUPS)
    one_row = margin + sum(group_width(ids) + group_gap for _n, ids in groups) + 2 * size + margin
    if one_row > right:
        groups = groups[:-1] + [("Display", ("menu:display",))]
    rows, x = [[]], margin
    for name, ids in groups:
        width = group_width(ids)
        if rows[-1] and x + width > right - margin:
            rows.append([])
            x = margin
        rows[-1].append((name, ids, x))
        x += width + group_gap
    if x + 2 * size + gap > right - margin:
        rows.append([])  # Inspector and Frame always stay reachable
    frame = True
    height = bar * len(rows)
    y0 = top - height
    buttons, separators = [], []
    for row_number, row in enumerate(rows):
        by = top - bar * (row_number + 1) + (bar - size) / 2
        for number, (_name, ids, gx) in enumerate(row):
            for k, identifier in enumerate(ids):
                data = _button(context, identifier, has_selection)
                buttons.append(Button(identifier, x=gx + k * (size + gap), y=by, w=size, h=size,
                                      **data))
            if number < len(row) - 1:
                separators.append((gx + group_width(ids) + group_gap / 2, by))
        if frame and row_number == len(rows) - 1:
            for k, identifier in enumerate(("toggle:show_inspector", "action:frame")):
                data = _button(context, identifier, has_selection)
                buttons.append(Button(identifier, x=right - margin - size * (k + 1) - gap * k,
                                      y=by, w=size, h=size, **data))
    return Strip(0, y0, right, height, tuple(buttons), tuple(separators))


def hit(context, region, px, py, has_selection=True):
    strip = layout(context, region, has_selection)
    if not strip.contains(px, py):
        return None
    for button in strip.buttons:
        if button.contains(px, py):
            return button
    return strip  # the strip itself swallows clicks so they never draw


def status_rect(context, region):
    s = style.ui_scale(context)
    return (0, 0, region.width - sidebar_overlap(context), style.STATUS * s)


def draw(context, region, hover=None, *, executing=False, error=False, has_selection=True):
    pal = style.palette(context)
    s = style.ui_scale(context)
    strip = layout(context, region, has_selection)
    style.rect(strip.x, strip.y, strip.w, strip.h, pal.bar)
    style.rect(strip.x, strip.y, strip.w, max(1.0, s), pal.bar_line)
    for x, by in strip.separators:
        style.rect(round(x), by + 4 * s, max(1.0, round(s)), style.BUTTON * s - 8 * s,
                   pal.bar_line)
    pixels = style.icon_pixels(context)
    accent = (*style.ACCENT, 1.0)
    for b in strip.buttons:
        hovered = hover == b.identifier and b.enabled
        if b.kind == "tool" and b.active:
            style.rounded(b.x, b.y, b.w, b.h, 3 * s, pal.active)
            bar_color = (*style.ERROR, 1.0) if error else accent
            style.rect(b.x + 5 * s, b.y, b.w - 10 * s, 2 * s, bar_color)
            if executing:
                style.disc(b.x + b.w - 5 * s, b.y + b.h - 5 * s, 2 * s, accent)
        elif hovered:
            style.rounded(b.x, b.y, b.w, b.h, 3 * s, pal.hover)
        if not b.enabled:
            color = (*pal.text[:3], 0.25)
        elif b.kind == "toggle" and not b.active:
            color = (*pal.text_dim[:3], 0.55)
        elif b.kind == "tool" and not b.active and not hovered:
            color = (*pal.text[:3], 0.82)
        else:
            color = pal.text
        style.draw_icon(b.icon, b.x + b.w / 2, b.y + b.h / 2, pixels, color)
        if b.kind == "menu":
            cx, cy = b.x + b.w - 4 * s, b.y + 5 * s
            style.lines_px([(cx - 2 * s, cy + 1.5 * s), (cx, cy - 0.5 * s),
                            (cx, cy - 0.5 * s), (cx + 2 * s, cy + 1.5 * s)],
                           color, max(1.0, s), region)
    if hover:
        tip = next((b for b in strip.buttons if b.identifier == hover), None)
        if tip is not None:
            _tooltip(context, region, tip, strip, pal, s)
    return strip


def _tooltip(context, region, b, strip, pal, s):
    title = b.label + (f" — {'On' if b.active else 'Off'}" if b.kind == "toggle" else "")
    width = max(style.text_width(title, 11, s) + style.text_width(b.shortcut, 10, s) + 24 * s,
                style.text_width(b.tooltip, 10, s) + 16 * s)
    width = min(width, region.width - 12 * s)
    height = 40 * s
    x = min(max(6 * s, b.x), region.width - width - 6 * s)
    y = strip.y - height - 4 * s
    style.rounded(x - 1, y - 1, width + 2, height + 2, 4 * s, pal.bar_line)
    style.rounded(x, y, width, height, 4 * s, pal.bar)
    style.text(title, x + 8 * s, y + 22 * s, pal.text, 11, scale=s)
    if b.shortcut:
        style.text(b.shortcut, x + width - 8 * s, y + 22 * s, pal.text_dim, 10, "RIGHT", s)
    style.text(style.fit(b.tooltip, width - 16 * s, 10, s), x + 8 * s, y + 7 * s,
               pal.text_dim, 10, scale=s)


def draw_status(context, region, tool, hint, message="", error=False, right_text=""):
    pal = style.palette(context)
    s = style.ui_scale(context)
    x, y, w, h = status_rect(context, region)
    style.rect(x, y, w, h, pal.bar)
    style.rect(x, y + h - max(1.0, s), w, max(1.0, s), pal.bar_line)
    baseline = y + (h - 10 * s) / 2 + 1 * s
    right_w = style.text_width(right_text, 10, s) if right_text else 0
    if right_text:
        style.text(right_text, w - 8 * s, baseline, pal.text_dim, 10, "RIGHT", s)
    available = w - right_w - 28 * s
    style.draw_icon(TOOL_ICONS.get(tool, "select"), 8 * s + 9 * s, y + h / 2,
                    18 if s < 1.25 else 27, pal.text)
    name = TOOL_LABELS.get(tool, tool)
    left = 30 * s
    used = style.text(style.fit(name, available - left, 11, s), left, baseline, pal.text, 11, scale=s)
    rest = available - left - used - 10 * s
    if rest > 40 * s:
        body = message or hint
        color = (*style.ERROR, 1.0) if (error and message) else pal.text_dim
        style.text(style.fit(body, rest, 10, s), left + used + 10 * s, baseline, color, 10, scale=s)
