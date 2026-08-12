# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Smoothly keep live-baked deformables inside every 3D viewport."""

from __future__ import annotations

import math
from dataclasses import dataclass

import bpy

from .addon_identity import addon_preferences


@dataclass
class _ViewState:
    center: tuple[float, float, float]
    distance: float
    frame: int


_states: dict[int, _ViewState] = {}


def interpolation_alpha(style: str, smoothing: float) -> float:
    """Return a stable per-frame blend factor for the selected motion style."""
    base = max(0.02, min(1.0, float(smoothing)))
    if style == "CINEMATIC":
        # A slower, ease-out camera drift; repeated application remains smooth.
        return 1.0 - (1.0 - base * 0.55) ** 2
    return 1.0 - (1.0 - base) ** 2


def _targets(plan):
    names = [getattr(item, "object_name", "")
             for item in getattr(plan, "deformables", ())]
    if not names:
        names = [getattr(plan, "object_name", "")]
    return [bpy.data.objects.get(name) for name in names
            if name and bpy.data.objects.get(name) is not None]


def _world_bounds(objects, depsgraph):
    try:
        from mathutils import Vector
    except ImportError:
        Vector = None
    points = []
    for obj in objects:
        try:
            evaluated = obj.evaluated_get(depsgraph)
        except (AttributeError, ReferenceError, RuntimeError):
            evaluated = obj
        matrix = getattr(
            evaluated, "matrix_world", getattr(obj, "matrix_world", None))
        for corner in getattr(evaluated, "bound_box", ()):
            try:
                local = Vector(corner) if Vector is not None else corner
                point = matrix @ local if matrix is not None else local
                points.append((float(point[0]), float(point[1]), float(point[2])))
            except (IndexError, TypeError, ValueError):
                continue
    if not points:
        return None
    low = tuple(min(point[i] for point in points) for i in range(3))
    high = tuple(max(point[i] for point in points) for i in range(3))
    center = tuple((low[i] + high[i]) * 0.5 for i in range(3))
    radius = max(math.dist(center, point) for point in points)
    return center, max(radius, 1.0e-4)


def _fit_distance(space, area, radius: float, margin: float) -> float:
    region = getattr(space, "region_3d", None)
    if (region is not None
            and getattr(region, "view_perspective", "PERSP") == "ORTHO"):
        aspect = max(1.0, float(getattr(area, "height", 1)) /
                     max(1.0, float(getattr(area, "width", 1))))
        return radius * margin * aspect
    lens = max(1.0, float(getattr(space, "lens", 50.0)))
    half_fov = math.atan(36.0 / (2.0 * lens))
    aspect = max(1.0, float(getattr(area, "height", 1)) /
                 max(1.0, float(getattr(area, "width", 1))))
    return radius * margin * aspect / max(0.05, math.tan(half_fov))


def update(plan, frame: int) -> None:
    """Frame live cloth in all open 3D viewports; safe to call every frame."""
    context = getattr(bpy, "context", None)
    if context is None:
        return
    try:
        prefs = addon_preferences(context, __package__)
    except (AttributeError, KeyError, RuntimeError):
        return
    if not getattr(prefs, "auto_frame_bake", False):
        return
    try:
        bounds = _world_bounds(_targets(plan), context.evaluated_depsgraph_get())
    except (AttributeError, ReferenceError, RuntimeError):
        return
    if bounds is None:
        return
    target_center, radius = bounds
    style = getattr(prefs, "auto_frame_style", "SMOOTH")
    alpha = interpolation_alpha(
        style, getattr(prefs, "auto_frame_smoothing", 0.18))
    margin = max(1.02, float(getattr(prefs, "auto_frame_margin", 1.25)))
    wm = getattr(context, "window_manager", None)
    for window in getattr(wm, "windows", ()):
        for area in getattr(getattr(window, "screen", None), "areas", ()):
            if getattr(area, "type", "") != "VIEW_3D":
                continue
            space = getattr(getattr(area, "spaces", None), "active", None)
            region = getattr(space, "region_3d", None)
            if region is None or getattr(region, "view_perspective", "") == "CAMERA":
                continue
            key = id(region)
            target_distance = _fit_distance(space, area, radius, margin)
            previous = _states.get(key)
            if previous is None or int(frame) <= previous.frame:
                current_center = tuple(float(v) for v in getattr(
                    region, "view_location", target_center))
                current_distance = float(getattr(
                    region, "view_distance", target_distance))
            else:
                current_center, current_distance = previous.center, previous.distance
            center = tuple(current_center[i] +
                           (target_center[i] - current_center[i]) * alpha
                           for i in range(3))
            blended_distance = current_distance + (
                target_distance - current_distance) * alpha
            # Pulling back is immediate so smoothing can never crop fast cloth;
            # moving closer remains damped and therefore visually calm.
            required_distance = target_distance + math.dist(
                center, target_center) * margin
            distance = max(blended_distance, required_distance)
            try:
                from mathutils import Vector
                region.view_location = Vector(center)
                region.view_distance = max(1.0e-4, distance)
                area.tag_redraw()
                _states[key] = _ViewState(center, distance, int(frame))
            except (AttributeError, ImportError, ReferenceError, RuntimeError,
                    TypeError):
                continue


def reset() -> None:
    _states.clear()
