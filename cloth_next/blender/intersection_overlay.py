# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport presentation for immutable solver intersection diagnostics."""

from __future__ import annotations

from .. import intersection_diagnostics
from ..ppf.coordinates import ppf_position_to_blender

_violations: tuple[intersection_diagnostics.IntersectionViolation, ...] = ()
_solver_input: intersection_diagnostics.SolverInputSnapshot | None = None
_index = 0
_show_input = False
_draw_handle = None
_label_handle = None


def violations():
    return _violations


def current():
    return _violations[_index] if _violations else None


def current_index() -> int:
    return _index


def set_violations(violations, solver_input=None) -> None:
    global _violations, _solver_input, _index
    _violations = tuple(violations)
    _solver_input = solver_input
    _index = 0
    _ensure_handler()
    _redraw()


def clear() -> None:
    global _violations, _solver_input, _index, _show_input
    _violations = ()
    _solver_input = None
    _index = 0
    _show_input = False
    _redraw()


def next_violation():
    global _index
    if _violations:
        _index = (_index + 1) % len(_violations)
    _redraw()
    return current()


def previous_violation():
    global _index
    if _violations:
        _index = (_index - 1) % len(_violations)
    _redraw()
    return current()


def solver_input_visible() -> bool:
    return _show_input


def toggle_solver_input() -> bool:
    global _show_input
    if _solver_input is not None:
        _show_input = not _show_input
    _ensure_handler()
    _redraw()
    return _show_input


def solver_input_snapshot():
    """Return the exact retained export snapshot, never a rebuilt viewport mesh."""
    return _solver_input


def _ensure_handler() -> None:
    global _draw_handle, _label_handle
    if not (_violations or _show_input):
        return
    try:
        import bpy
        space = bpy.types.SpaceView3D
        if _draw_handle is None:
            _draw_handle = space.draw_handler_add(
                _draw, (), "WINDOW", "POST_VIEW")
        if _label_handle is None:
            _label_handle = space.draw_handler_add(
                _draw_label, (), "WINDOW", "POST_PIXEL")
    except (AttributeError, RuntimeError, ImportError):
        _draw_handle = None


def _redraw() -> None:
    try:
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except (AttributeError, RuntimeError, ImportError):
        pass


def _triangles_for_draw():
    if _show_input and _solver_input is not None:
        return tuple(
            (tuple(ppf_position_to_blender(v) for v in item.vertices),
             (0.15, 0.55, 1.0, 0.12) if item.owner.role != "COLLIDER"
             else (1.0, 0.45, 0.1, 0.12))
            for item in _solver_input.triangles)
    violation = current()
    if violation is None:
        return ()
    colors = ((1.0, 0.12, 0.08, 0.48), (0.05, 0.55, 1.0, 0.48))
    return tuple(
        (tuple(ppf_position_to_blender(v) for v in element.vertices),
         colors[index % len(colors)])
        for index, element in enumerate(violation.elements))


def _draw() -> None:
    """Draw both reported sides through geometry, with distinct outlines."""
    triangles = _triangles_for_draw()
    if not triangles:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        for vertices, color in triangles:
            shader.uniform_float("color", color)
            batch_for_shader(shader, "TRIS", {"pos": vertices}).draw(shader)
            outline = (vertices[0], vertices[1], vertices[1], vertices[2],
                       vertices[2], vertices[0])
            line = batch_for_shader(shader, "LINES", {"pos": outline})
            shader.uniform_float("color", (*color[:3], 1.0))
            gpu.state.line_width_set(3.0)
            line.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.depth_test_set("LESS_EQUAL")
        gpu.state.blend_set("NONE")
    except (AttributeError, RuntimeError, ImportError, ValueError):
        return


def label_lines() -> tuple[str, ...]:
    violation = current()
    if violation is None:
        return ("Solver Input",) if _show_input else ()
    title = violation.classification.replace("_", " ").title()
    lines = [title]
    for element in violation.elements:
        face = (element.source_polygon_index
                if element.source_polygon_index is not None
                else element.local_triangle_index)
        proxy = " · generated proxy" if element.generated_proxy else ""
        lines.append(f"{element.object_name} · Triangle {face}{proxy}")
    lines.append(f"{_index + 1} of {violation.total_count}")
    return tuple(lines)


def _draw_label() -> None:
    lines = label_lines()
    if not lines:
        return
    try:
        import blf
        font = 0
        blf.size(font, 15)
        for index, line in enumerate(lines):
            blf.position(font, 28, 92 - index * 21, 0)
            blf.color(font, 1.0, 1.0, 1.0, 1.0)
            blf.draw(font, line)
    except (AttributeError, RuntimeError, ImportError):
        return
