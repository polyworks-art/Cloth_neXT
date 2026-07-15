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
            viewport_width=viewport_width, viewport_height=viewport_height)
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")

        def batch(mode, positions, color):
            geometry = batch_for_shader(shader, mode, {"pos": positions})
            shader.bind()
            shader.uniform_float("color", color)
            geometry.draw(shader)

        def rect(x, y, width, height, color):
            batch("TRI_FAN", ((x, y), (x + width, y),
                              (x + width, y + height), (x, y + height)), color)

        rect(card.x, card.y, card.width, card.height, (.035, .04, .05, .9))
        rect(card.x, card.y, 4 * card.scale, card.height, (.92, .55, .12, 1))
        font = 0
        left = card.x + 14 * card.scale
        top = card.y + card.height - 24 * card.scale
        blf.size(font, round(13 * card.scale))
        blf.color(font, .94, .94, .94, 1)
        blf.position(font, left, top, 0)
        blf.draw(font, "System Load")

        graph_x = card.x + 145 * card.scale
        graph_width = max(20, card.width - 159 * card.scale)
        row_height = 43 * card.scale
        graph_height = 28 * card.scale
        colors = {"cpu": (.35, .72, 1.0, 1),
                  "ram": (.55, .88, .48, 1),
                  "vram": (.95, .58, .22, 1)}
        for index, metric in enumerate(card.metrics):
            row_top = top - (25 + index * 43) * card.scale
            blf.size(font, round(11 * card.scale))
            blf.color(font, .82, .84, .87, 1)
            blf.position(font, left, row_top, 0)
            blf.draw(font, metric.label)
            blf.size(font, round(9 * card.scale))
            blf.color(font, .63, .66, .7, 1)
            blf.position(font, left, row_top - 15 * card.scale, 0)
            blf.draw(font, metric.value)

            graph_y = row_top - 14 * card.scale
            rect(graph_x, graph_y, graph_width, graph_height,
                 (.075, .085, .1, .95))
            values = list(_history.series[metric.key])
            valid = [(sample_index, value) for sample_index, value
                     in enumerate(values) if value is not None]
            if len(valid) >= 2:
                denominator = max(1, len(values) - 1)
                points = tuple(
                    (graph_x + graph_width * sample_index / denominator,
                     graph_y + graph_height * value)
                    for sample_index, value in valid)
                batch("LINE_STRIP", points, colors[metric.key])
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


def unregister():
    global _handle
    shared_telemetry.stop()
    _history.clear()
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
