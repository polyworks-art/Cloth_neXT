# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep live-baked cloth in view without stepping the viewport per frame."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import bpy

from .addon_identity import addon_preferences


_TICK_SECONDS = 1.0 / 60.0
_CENTER_DEAD_ZONE = 0.012
_DISTANCE_DEAD_ZONE = 0.012


@dataclass
class _ViewState:
    region: object
    area: object
    center: tuple[float, float, float]
    distance: float
    target_center: tuple[float, float, float]
    target_distance: float
    response: float
    style: str
    last_time: float


_states: dict[int, _ViewState] = {}
_timer_running = False


def interpolation_alpha(style: str, smoothing: float,
                        elapsed: float = _TICK_SECONDS) -> float:
    """Return a refresh-rate-independent blend factor.

    ``smoothing`` retains its old meaning at 60 Hz, while a slow or busy UI no
    longer changes the apparent camera response.
    """
    base = max(0.02, min(1.0, float(smoothing)))
    if style == "CINEMATIC":
        base *= 0.38
    if base >= 1.0:
        return 1.0
    ticks = max(0.0, min(0.1, float(elapsed))) / _TICK_SECONDS
    return 1.0 - (1.0 - base) ** ticks


def stabilized_target(previous: float, target: float,
                      dead_zone: float) -> float:
    """Ignore tiny bound-box noise which otherwise causes camera hunting."""
    return previous if abs(target - previous) <= max(0.0, dead_zone) else target


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
                points.append(tuple(float(point[i]) for i in range(3)))
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
    aspect = max(1.0, float(getattr(area, "height", 1)) /
                 max(1.0, float(getattr(area, "width", 1))))
    if (region is not None
            and getattr(region, "view_perspective", "PERSP") == "ORTHO"):
        return radius * margin * aspect
    lens = max(1.0, float(getattr(space, "lens", 50.0)))
    half_fov = math.atan(36.0 / (2.0 * lens))
    return radius * margin * aspect / max(0.05, math.tan(half_fov))


def _schedule_timer() -> None:
    global _timer_running
    if _timer_running:
        return
    timers = getattr(getattr(bpy, "app", None), "timers", None)
    if timers is None:
        return
    try:
        timers.register(_tick, first_interval=_TICK_SECONDS)
        _timer_running = True
    except (AttributeError, RuntimeError, ValueError):
        pass


def _tick():
    """Animate between solver updates instead of jumping on each update."""
    global _timer_running
    now = time.monotonic()
    moving = False
    stale = []
    try:
        from mathutils import Vector
    except ImportError:
        Vector = None
    for key, state in tuple(_states.items()):
        try:
            elapsed = max(_TICK_SECONDS, min(0.1, now - state.last_time))
            alpha = interpolation_alpha(
                state.style, state.response, elapsed)
            center = tuple(
                state.center[i] +
                (state.target_center[i] - state.center[i]) * alpha
                for i in range(3))
            distance = state.distance + (
                state.target_distance - state.distance) * alpha
            state.region.view_location = (
                Vector(center) if Vector is not None else center)
            state.region.view_distance = max(1.0e-4, distance)
            state.area.tag_redraw()
            state.center = center
            state.distance = distance
            state.last_time = now
            center_error = math.dist(center, state.target_center)
            distance_error = abs(distance - state.target_distance)
            moving |= (center_error > max(1.0e-5, state.target_distance * 1e-4)
                       or distance_error > max(1.0e-5,
                                               state.target_distance * 1e-4))
        except (AttributeError, ReferenceError, RuntimeError, TypeError,
                ValueError):
            stale.append(key)
    for key in stale:
        _states.pop(key, None)
    if moving:
        return _TICK_SECONDS
    _timer_running = False
    return None


def update(plan, frame: int) -> None:
    """Publish new framing targets; the timer performs the visible motion."""
    del frame  # Solver frame cadence must not control viewport motion cadence.
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
    style = str(getattr(prefs, "auto_frame_style", "SMOOTH"))
    response = float(getattr(prefs, "auto_frame_smoothing", 0.18))
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
            state = _states.get(key)
            if state is None:
                center = tuple(float(v) for v in getattr(
                    region, "view_location", target_center))
                distance = float(getattr(
                    region, "view_distance", target_distance))
                state = _ViewState(
                    region, area, center, distance, target_center,
                    target_distance, response, style, time.monotonic())
                _states[key] = state
            else:
                center_zone = max(radius * _CENTER_DEAD_ZONE, 1.0e-5)
                state.target_center = tuple(stabilized_target(
                    state.target_center[i], target_center[i], center_zone)
                    for i in range(3))
                state.target_distance = stabilized_target(
                    state.target_distance, target_distance,
                    max(target_distance * _DISTANCE_DEAD_ZONE, 1.0e-5))
                state.response = response
                state.style = style
                state.region = region
                state.area = area
    _schedule_timer()


def reset() -> None:
    global _timer_running
    timers = getattr(getattr(bpy, "app", None), "timers", None)
    if timers is not None:
        try:
            if timers.is_registered(_tick):
                timers.unregister(_tick)
        except (AttributeError, RuntimeError, ValueError):
            pass
    _states.clear()
    _timer_running = False
