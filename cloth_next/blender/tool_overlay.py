# SPDX-License-Identifier: GPL-3.0-or-later
"""Draws modal editor toolbars in the Cloth NeXt floating-toolbar style.

A column of round icon buttons, exactly as wide as Blender's navigation
buttons, hangs below them on the right edge of the 3D View and moves with the
sidebar (N panel). The top of the viewport stays free for other add-ons.

Icons: ``assets/icons/tool_<icon>.png`` is used when present; otherwise a
vector placeholder from ``tool_icons`` is drawn.
"""
from __future__ import annotations

from pathlib import Path

from .. import tool_icons, tool_overlay
from ..tool_overlay import SEPARATOR, ToolButton  # noqa: F401 - re-exported

_ACTIVE_ICON = (0.02, 0.05, 0.07, 1.0)
_TOGGLE_ON = (0.06, 0.66, 0.85, 0.40)
_HOVER = (1.0, 1.0, 1.0, 0.12)
_DANGER = (0.78, 0.19, 0.22, 1.0)
_ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
_png_exists = {}


def ui_scale(context):
    try:
        return max(.75, float(context.preferences.system.ui_scale))
    except AttributeError:
        return 1.0


def header_overlap(context):
    """Height of transparent headers drawn over the top of the WINDOW region."""
    region, area = context.region, context.area
    top = region.y + region.height
    overlap = 0
    for other in getattr(area, "regions", ()):
        if other.type in {"HEADER", "TOOL_HEADER"} and other.height > 1:
            if region.y <= other.y < top:
                overlap = max(overlap, top - other.y)
    return overlap


def sidebar_overlap(context):
    """Width of an overlapping sidebar (N panel) covering the right edge."""
    region, area = context.region, context.area
    right = region.x + region.width
    overlap = 0
    for other in getattr(area, "regions", ()):
        if other.type == "UI" and other.width > 1 and region.x < other.x < right:
            overlap = max(overlap, right - other.x)
    return overlap


def anchor(context):
    """(centre x, top, bottom) of the column in region pixels."""
    region = context.region
    scale = ui_scale(context)
    gizmo_size = float(getattr(context.preferences.view, "gizmo_size_navigate_v3d", 80))
    space = context.space_data
    show_navigation = bool(getattr(space, "show_gizmo", True)
                           and getattr(space, "show_gizmo_navigate", True))
    top = region.height - header_overlap(context)
    right = region.width - sidebar_overlap(context)
    if show_navigation:
        center = tool_overlay.navigation_center(right, gizmo_size=gizmo_size, ui_scale=scale)
    else:
        center = right - (tool_overlay.MARGIN + tool_overlay.BUTTON / 2) * scale
    column_top = tool_overlay.navigation_bottom(
        top, gizmo_size=gizmo_size, ui_scale=scale, show_navigation=show_navigation)
    # Keep clear of the Cloth NeXt simulation toolbar at the bottom.
    return center, column_top, 96 * scale


def build_layout(context, items):
    center, top, bottom = anchor(context)
    return tool_overlay.vertical_layout(items, center, top, bottom, ui_scale(context))


def _bubble(blf, shader, batch_for_shader, floating, text, right, center_y, size,
            color, factor):
    """Right-aligned text bubble ending at ``right``."""
    blf.size(0, size)
    width, _height = blf.dimensions(0, text)
    pad = 8 * factor
    height = size + pad * 1.4
    x = right - width - pad * 2
    y = center_y - height / 2
    floating._rounded(shader, batch_for_shader, x, y, width + pad * 2, height, height / 2,
                      (*floating._BG[:3], .88))
    floating._label(blf, text, x + pad, y + height / 2 - size * .36, size, color)


def _icon_png(name):
    if name not in _png_exists:
        _png_exists[name] = (_ICON_DIR / f"tool_{name}.png").is_file()
    return _png_exists[name]


def _draw_glyph(gpu, batch_for_shader, floating, blf, placed, color, region):
    button = placed.button
    cx, cy = placed.x + placed.width / 2, placed.y + placed.height / 2
    half = placed.width * .29
    if button.icon and _icon_png(button.icon):
        size = placed.width * .7
        floating._asset_icon(gpu, batch_for_shader, f"tool_{button.icon}",
                             cx - size / 2, cy - size / 2, size)
        return
    if button.icon:
        glyph = tool_icons.icon(button.icon)
        lines = []
        for stroke in glyph["strokes"]:
            points = [(cx + x * half, cy + y * half, 0.0) for x, y in stroke]
            for a, b in zip(points, points[1:]):
                lines.extend((a, b))
        if lines:
            shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
            shader.bind()
            shader.uniform_float("viewportSize", (region.width, region.height))
            shader.uniform_float("lineWidth", max(1.0, 1.5 * placed.width / 24))
            shader.uniform_float("color", color)
            batch_for_shader(shader, "LINES", {"pos": lines}).draw(shader)
        flat = gpu.shader.from_builtin("UNIFORM_COLOR")
        for x, y, radius in glyph["dots"]:
            r = radius * half
            floating._rounded(flat, batch_for_shader, cx + x * half - r, cy + y * half - r,
                              2 * r, 2 * r, r, color)
        return
    size = max(7, round(placed.height * (.42 if len(button.label) > 2 else .52)))
    blf.size(0, size)
    width, _height = blf.dimensions(0, button.label)
    floating._label(blf, button.label, cx - width / 2, cy - size * .36, size, color)


def draw(context, items, hint="", hovered=None, *, warning=False):
    """Paint the toolbar for the current region and return its layout."""
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    from . import floating_simulation as floating

    layout = build_layout(context, items)
    f = layout.scale
    region = context.region
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    old_blend = gpu.state.blend_get()
    gpu.state.blend_set("ALPHA")
    try:
        for y in layout.separators:
            shader.bind()
            shader.uniform_float("color", (0.56, 0.63, 0.68, 0.45))
            batch_for_shader(shader, "LINES", {"pos": (
                (layout.x + layout.width * .25, y), (layout.x + layout.width * .75, y))}).draw(shader)
        for placed in layout.buttons:
            button = placed.button
            fill, color = floating._BG, floating._TEXT
            if button.kind == "mode" and button.active:
                fill, color = floating._CYAN, _ACTIVE_ICON
            elif button.kind == "toggle":
                fill = (*floating._CYAN[:3], 1.0) if button.active else floating._BG
                color = _ACTIVE_ICON if button.active else floating._MUTED
            elif button.kind == "primary":
                fill = floating._BLUE if button.enabled else floating._BG
            elif button.kind == "danger":
                fill = _DANGER if button.enabled else floating._BG
            elif button.kind == "value":
                fill = (*floating._SURFACE[:3], .94)
            if not button.enabled:
                color = (*floating._MUTED[:3], .42)
            radius = placed.height / 2
            floating._rounded(shader, batch_for_shader, placed.x, placed.y,
                              placed.width, placed.height, radius, fill)
            if hovered == button.identifier and button.enabled and button.kind != "value":
                floating._rounded(shader, batch_for_shader, placed.x, placed.y,
                                  placed.width, placed.height, radius, _HOVER)
            _draw_glyph(gpu, batch_for_shader, floating, blf, placed, color, region)
            gpu.state.blend_set("ALPHA")
        size = max(8, round(11 * f))
        gap = 8 * f
        tooltip = layout.placed(hovered) if hovered else None
        if tooltip is not None:
            text = tooltip.button.label
            if tooltip.button.tooltip:
                text = f"{text}  ·  {tooltip.button.tooltip}"
            _bubble(blf, shader, batch_for_shader, floating, text, layout.x - gap,
                    tooltip.y + tooltip.height / 2, size, floating._TEXT, f)
        if hint:
            # Instructions sit beside the column top, never over other add-ons.
            hint_y = layout.y + layout.height - layout.width / 2
            if tooltip is not None and abs(tooltip.y + tooltip.height / 2 - hint_y) < 26 * f:
                hint_y -= 30 * f
            _bubble(blf, shader, batch_for_shader, floating, hint, layout.x - gap, hint_y,
                    size, (1.0, .45, .45, 1.0) if warning else floating._TEXT, f)
    finally:
        gpu.state.blend_set(old_blend)
    return layout
