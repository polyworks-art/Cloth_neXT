# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw-only CPU, RAM and VRAM history graphs for an active Bake."""
from __future__ import annotations

import bpy

from ..bake.controller import shared_controller
from ..bake.status import BakeState
from ..telemetry import shared_telemetry
from ..telemetry.hud_layout import ResourceHistory, build_resource_card

_handle = None
_draw_failed = False
_history = ResourceHistory()

# Public website palette, mirrored in the viewport overlay.
HUD_BG = (.027, .063, .055, .96)          # #07100e
HUD_SURFACE = (.067, .110, .098, .98)     # #111c19
HUD_BORDER = (.808, 1.0, .929, .18)       # rgba(206, 255, 237, .18)
HUD_TEXT = (.937, .984, .969, 1.0)        # #effbf7
HUD_MUTED = (.616, .694, .667, 1.0)       # #9db1aa
HUD_MINT = (.329, .937, .765, 1.0)        # #54efc3
HUD_MINT_BRIGHT = (.620, 1.0, .875, 1.0)  # #9effdf
HUD_TEAL = (.173, .655, .514, 1.0)
HUD_DANGER = (1.0, .420, .443, .96)       # #ff6b71


def _redraw_pulse():
    """Refresh active resource graphs independently of viewport input."""
    prefs = _preferences()
    if (shared_controller.snapshot().state is not BakeState.IDLE
            and (prefs is None or getattr(prefs, "show_bake_hud", True))):
        windows = getattr(
            getattr(bpy.context, "window_manager", None), "windows", ())
        for window in windows:
            for area in getattr(getattr(window, "screen", None), "areas", ()):
                if getattr(area, "type", "") == "VIEW_3D":
                    area.tag_redraw()
    return max(.25, min(10.0, float(getattr(
        prefs, "telemetry_refresh_seconds", 1.0))))


def _preferences():
    try:
        return bpy.context.preferences.addons[
            __package__.partition(".blender")[0]].preferences
    except (KeyError, AttributeError):
        return None


def _draw():
    global _draw_failed
    if _draw_failed:
        return
    try:
        import blf
        import gpu
        from gpu_extras.batch import batch_for_shader

        prefs = _preferences()
        if (shared_controller.snapshot().state is BakeState.IDLE
                or (prefs and not prefs.show_bake_hud)):
            return
        telemetry = shared_telemetry.snapshot()
        _history.sample(telemetry)
        viewport_width = getattr(bpy.context.region, "width", 800)
        viewport_height = getattr(bpy.context.region, "height", 600)
        card = build_resource_card(
            telemetry, anchor=getattr(prefs, "bake_hud_anchor", "BOTTOM_LEFT"),
            scale=getattr(prefs, "bake_hud_scale", 1.0),
            viewport_width=viewport_width, viewport_height=viewport_height,
            ram_limit_percent=(getattr(prefs,"auto_cancel_ram_percent",90)
                if getattr(prefs,"auto_cancel_high_ram",True) else None))
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        def batch(mode, positions, color):
            geometry = batch_for_shader(shader, mode, {"pos": positions})
            shader.bind()
            shader.uniform_float("color", color)
            geometry.draw(shader)

        def rect(x, y, width, height, color):
            batch("TRI_FAN", ((x, y), (x + width, y),
                              (x + width, y + height), (x, y + height)), color)

        rect(card.x-1, card.y-1, card.width+2, card.height+2, HUD_BORDER)
        rect(card.x, card.y, card.width, card.height, HUD_BG)
        rect(card.x, card.y, 3 * card.scale, card.height, HUD_MINT)
        font = 0
        left = card.x + 14 * card.scale
        top = card.y + card.height - 24 * card.scale
        blf.size(font, round(13 * card.scale))
        blf.color(font, *HUD_TEXT)
        blf.position(font, left, top, 0)
        blf.draw(font, "System Load")
        blf.size(font, round(8 * card.scale))
        blf.color(font, *HUD_MINT)
        blf.position(font, card.x+card.width-38*card.scale, top, 0)
        blf.draw(font, "● LIVE")

        graph_x = card.x + 145 * card.scale
        graph_width = max(20, card.width - 159 * card.scale)
        row_height = 43 * card.scale
        graph_height = 28 * card.scale
        colors = {"cpu": HUD_MINT_BRIGHT,
                  "ram": HUD_MINT,
                  "vram": HUD_TEAL}
        for index, metric in enumerate(card.metrics):
            row_top = top - (25 + index * 43) * card.scale
            blf.size(font, round(11 * card.scale))
            blf.color(font, *HUD_TEXT)
            blf.position(font, left, row_top, 0)
            blf.draw(font, metric.label)
            blf.size(font, round(9 * card.scale))
            blf.color(font, *HUD_MUTED)
            blf.position(font, left, row_top - 15 * card.scale, 0)
            blf.draw(font, metric.value)

            graph_y = row_top - 14 * card.scale
            rect(graph_x, graph_y, graph_width, graph_height, HUD_SURFACE)
            for grid_fraction in (.25,.5,.75):
                grid_y=graph_y+graph_height*grid_fraction
                batch("LINES",((graph_x,grid_y),(graph_x+graph_width,grid_y)),
                      HUD_BORDER[:3]+(.32,))
            values = list(_history.series[metric.key])
            valid = [(sample_index, value) for sample_index, value
                     in enumerate(values) if value is not None]
            if len(valid) >= 2:
                denominator = max(1, len(values) - 1)
                points = tuple(
                    (graph_x + graph_width * sample_index / denominator,
                     graph_y + graph_height * value)
                    for sample_index, value in valid)
                fill_points=((points[0][0],graph_y),)+points+(
                    (points[-1][0],graph_y),)
                batch("TRI_FAN",fill_points,
                      colors[metric.key][:3]+(.10,))
                batch("LINE_STRIP", points, colors[metric.key])
                dot_x,dot_y=points[-1]
                rect(dot_x-1.5*card.scale,dot_y-1.5*card.scale,
                     3*card.scale,3*card.scale,colors[metric.key])
            if metric.key=="ram" and card.ram_limit_fraction is not None:
                limit_y=graph_y+graph_height*card.ram_limit_fraction
                dash=8*card.scale; gap=4*card.scale; cursor=graph_x
                while cursor<graph_x+graph_width:
                    end=min(cursor+dash,graph_x+graph_width)
                    batch("LINES",((cursor,limit_y),(end,limit_y)),
                          HUD_DANGER)
                    cursor=end+gap
    except Exception:
        _draw_failed = True


def register():
    global _handle, _draw_failed
    shared_telemetry.start()
    if _handle is not None or not hasattr(bpy.types, "SpaceView3D"):
        return
    _draw_failed = False
    _history.clear()
    _handle = bpy.types.SpaceView3D.draw_handler_add(
        _draw, (), "WINDOW", "POST_PIXEL")
    if not bpy.app.timers.is_registered(_redraw_pulse):
        bpy.app.timers.register(_redraw_pulse, first_interval=.25)


def unregister():
    global _handle
    shared_telemetry.stop()
    _history.clear()
    if bpy.app.timers.is_registered(_redraw_pulse):
        bpy.app.timers.unregister(_redraw_pulse)
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
