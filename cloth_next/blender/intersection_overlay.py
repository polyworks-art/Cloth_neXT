# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport presentation for immutable solver intersection diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from math import isfinite

from .. import intersection_diagnostics
from ..core.logging import get_logger, log_with_context
from ..ppf.coordinates import ppf_position_to_blender

_violations: tuple[intersection_diagnostics.IntersectionViolation, ...] = ()
_degenerate_faces: tuple[object, ...] = ()
_solver_input: intersection_diagnostics.SolverInputSnapshot | None = None
_diagnostic_session = None
_detected_count = 0
_mapping_warning = ""
_detail_notice = ""
_index = 0
_show_input = False
_draw_handle = None
_label_handle = None
_logger = get_logger("viewport.intersections")


@dataclass(frozen=True, slots=True)
class DrawPrimitive:
    """Pure GPU-independent viewport primitive produced from diagnostics."""

    mode: str
    vertices: tuple[tuple[float, float, float], ...]
    color: tuple[float, float, float, float]
    line_width: float = 1.0
    point_size: float = 1.0


def violations():
    return _violations


def current():
    items = presentation_diagnostics()
    return items[_index] if items else None


def current_index() -> int:
    return _index


def diagnostic_session():
    """Return the authoritative retained diagnostic result, when supplied."""
    return _diagnostic_session


def detected_count() -> int:
    return _detected_count


def mapped_count() -> int:
    return len(_violations)


def presentation_diagnostics() -> tuple:
    """Drawable mapped intersections followed by degenerate source faces."""
    return (*_violations, *_degenerate_faces)


def mapping_warning() -> str:
    return _mapping_warning


def detail_notice() -> str:
    return _detail_notice


def _session_values(session):
    values = getattr(session, "mapped_violations", None)
    if values is None:
        values = getattr(session, "violations", ())
    solver_input = getattr(
        session, "snapshot", getattr(
            session, "solver_input",
            getattr(session, "solver_input_snapshot", None)))
    total = getattr(
        session, "detected_count", getattr(session, "total_count", None))
    warning = getattr(
        session, "mapping_warning", getattr(session, "warning", ""))
    notice = getattr(session, "detail_notice", "")
    degenerates = tuple(getattr(session, "degenerate_faces", ()) or ())
    return (tuple(values or ()), degenerates, solver_input, total,
            str(warning or ""), str(notice or ""))


def set_diagnostic_session(session, solver_input=None) -> None:
    """Present one immutable authoritative diagnostics session.

    The adapter intentionally uses the small public result surface rather
    than depending on a particular session dataclass. This keeps Blender UI
    compatible with old callers while the mapping pipeline remains the sole
    source of truth for detected and unmapped counts.
    """
    global _diagnostic_session
    values, degenerates, retained_input, total, warning, notice = (
        _session_values(session))
    _diagnostic_session = session
    _set_state(
        values, solver_input if solver_input is not None else retained_input,
        detected=total, warning=warning, notice=notice,
        degenerate_faces=degenerates)


def set_violations(violations, solver_input=None) -> None:
    """Compatibility wrapper for callers without a diagnostic session."""
    global _diagnostic_session
    values = tuple(violations)
    total = max(
        (int(item.total_count) for item in values), default=len(values))
    _diagnostic_session = None
    _set_state(values, solver_input, detected=total, warning="", notice="",
               degenerate_faces=())


def _set_state(violations, solver_input, *, detected, warning,
               notice, degenerate_faces) -> None:
    global _violations, _solver_input, _index, _detected_count
    global _degenerate_faces, _mapping_warning, _detail_notice
    # A converted violation is only useful to the overlay when at least one of
    # its mapped elements still contains a complete, finite triangle. Keeping
    # count-only records here produces a label without any corresponding GPU
    # geometry and overstates the number of mapped violations.
    _violations = tuple(
        item for item in violations if _violation_is_drawable(item))
    _degenerate_faces = tuple(
        item for item in degenerate_faces if _violation_is_drawable(item))
    _solver_input = solver_input
    try:
        _detected_count = max(len(_violations), int(detected))
    except (TypeError, ValueError):
        _detected_count = len(_violations)
    _mapping_warning = warning
    _detail_notice = notice
    _index = 0
    if presentation_diagnostics() or _detected_count:
        # Blender can retire draw handlers during file/workspace lifecycle
        # changes without making the opaque Python token falsy.  Re-arm the
        # pair for every newly published immutable result so a stale non-None
        # token can never suppress later diagnostics.
        _remove_handlers()
        _ensure_handler()
    else:
        _remove_handlers()
    _redraw()


def clear() -> None:
    global _violations, _degenerate_faces, _solver_input, _diagnostic_session
    global _detected_count, _mapping_warning, _detail_notice, _index, _show_input
    _violations = ()
    _degenerate_faces = ()
    _solver_input = None
    _diagnostic_session = None
    _detected_count = 0
    _mapping_warning = ""
    _detail_notice = ""
    _index = 0
    _show_input = False
    _remove_handlers()
    _redraw()


def reset_runtime() -> None:
    """Forget file-specific data and opaque draw tokens at a load boundary."""
    clear()


def _remove_handlers() -> None:
    """Remove callbacks as well as state; safe across reload/unregister."""
    global _draw_handle, _label_handle
    handles = (_draw_handle, _label_handle)
    # Clear first so repeated/re-entrant cleanup can never remove a handle
    # twice, including during add-on unregister and module reload.
    _draw_handle = None
    _label_handle = None
    try:
        import bpy
        space = bpy.types.SpaceView3D
        for handle in handles:
            if handle is not None:
                try:
                    space.draw_handler_remove(handle, "WINDOW")
                except (ReferenceError, ValueError, RuntimeError) as exc:
                    log_with_context(
                        _logger, logging.WARNING,
                        "Viewport intersection handler removal failed", {
                            "handle": repr(handle),
                            "error": str(exc),
                        })
    except (AttributeError, RuntimeError, ImportError) as exc:
        log_with_context(
            _logger, logging.DEBUG,
            "Viewport intersection handlers unavailable", {
                "error": str(exc),
            })


def next_violation():
    global _index
    items = presentation_diagnostics()
    if items:
        _index = (_index + 1) % len(items)
    _redraw()
    return current()


def previous_violation():
    global _index
    items = presentation_diagnostics()
    if items:
        _index = (_index - 1) % len(items)
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
    if not (presentation_diagnostics() or _show_input):
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
    except (AttributeError, RuntimeError, ImportError) as exc:
        # Handler installation is a pair: never lose track of a successfully
        # installed geometry callback when label callback installation fails.
        _remove_handlers()
        log_with_context(
            _logger, logging.WARNING,
            "Viewport intersection handler installation failed", {
                "error": str(exc),
            })


def _redraw() -> None:
    try:
        import bpy
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except (AttributeError, RuntimeError, ImportError):
        pass


def _triangle_is_drawable(vertices) -> bool:
    try:
        if len(vertices) != 3:
            return False
        return all(
            len(vertex) == 3
            and all(isfinite(float(component)) for component in vertex)
            for vertex in vertices)
    except (TypeError, ValueError):
        return False


def _violation_is_drawable(violation) -> bool:
    elements = getattr(violation, "elements", None)
    if elements is None:
        return _triangle_is_drawable(getattr(violation, "vertices", ()))
    return any(
        _triangle_is_drawable(element.vertices)
        for element in elements)


def _triangle_is_degenerate(vertices, *, epsilon=1.0e-24) -> bool:
    if not _triangle_is_drawable(vertices):
        return False
    a, b, c = (tuple(map(float, vertex)) for vertex in vertices)
    ab = tuple(b[index] - a[index] for index in range(3))
    ac = tuple(c[index] - a[index] for index in range(3))
    cross = (ab[1] * ac[2] - ab[2] * ac[1],
             ab[2] * ac[0] - ab[0] * ac[2],
             ab[0] * ac[1] - ab[1] * ac[0])
    return sum(value * value for value in cross) <= epsilon


def primitives_for_diagnostic(
        violation=None, *, solver_input=None,
        show_solver_input=False) -> tuple[DrawPrimitive, ...]:
    """Return deterministic draw commands without importing Blender GPU APIs."""
    if show_solver_input and solver_input is not None:
        elements = tuple(
            (item.vertices,
             (0.15, 0.55, 1.0, 0.12)
             if item.owner.role != "COLLIDER"
             else (1.0, 0.45, 0.1, 0.12))
            for item in solver_input.triangles)
    elif violation is not None:
        colors = ((1.0, 0.12, 0.08, 0.48),
                  (0.05, 0.55, 1.0, 0.48))
        diagnostic_elements = getattr(violation, "elements", None)
        if diagnostic_elements is None:
            elements = ((violation.vertices, (1.0, 0.45, 0.05, 0.55)),)
        else:
            elements = tuple(
                (element.vertices, colors[index % len(colors)])
                for index, element in enumerate(diagnostic_elements))
    else:
        elements = ()
    primitives = []
    for source_vertices, color in elements:
        if not _triangle_is_drawable(source_vertices):
            continue
        vertices = tuple(
            ppf_position_to_blender(vertex) for vertex in source_vertices)
        primitives.append(DrawPrimitive("TRIS", vertices, color))
        outline = (vertices[0], vertices[1], vertices[1], vertices[2],
                   vertices[2], vertices[0])
        solid = (*color[:3], 1.0)
        primitives.append(DrawPrimitive(
            "LINES", outline, solid, line_width=3.0))
        if _triangle_is_degenerate(source_vertices):
            # A zero-area fill is invisible. Points plus the outline keep the
            # authoritative one-triangle diagnostic visible without inventing
            # geometry or implying a second intersecting surface.
            primitives.append(DrawPrimitive(
                "POINTS", vertices, (1.0, 0.65, 0.05, 1.0),
                point_size=7.0))
    return tuple(primitives)


def primitives_for_diagnostics(
        diagnostics, *, solver_input=None,
        show_solver_input=False) -> tuple[DrawPrimitive, ...]:
    """Aggregate the existing per-diagnostic primitives deterministically."""
    if show_solver_input:
        return primitives_for_diagnostic(
            solver_input=solver_input, show_solver_input=True)
    return tuple(
        primitive
        for diagnostic in diagnostics
        for primitive in primitives_for_diagnostic(diagnostic))


def _triangles_for_draw():
    return tuple(
        (primitive.vertices, primitive.color)
        for primitive in primitives_for_diagnostics(
            presentation_diagnostics(), solver_input=_solver_input,
            show_solver_input=_show_input)
        if primitive.mode == "TRIS")


def _draw() -> None:
    """Draw both reported sides through geometry, with distinct outlines."""
    primitives = primitives_for_diagnostics(
        presentation_diagnostics(), solver_input=_solver_input,
        show_solver_input=_show_input)
    if not primitives:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        gpu.state.depth_test_set("NONE")
        for primitive in primitives:
            shader.uniform_float("color", primitive.color)
            if primitive.mode == "LINES":
                gpu.state.line_width_set(primitive.line_width)
            elif primitive.mode == "POINTS":
                gpu.state.point_size_set(primitive.point_size)
            batch_for_shader(
                shader, primitive.mode,
                {"pos": primitive.vertices}).draw(shader)
    except (AttributeError, RuntimeError, ImportError, ValueError):
        return
    finally:
        try:
            gpu.state.line_width_set(1.0)
            gpu.state.point_size_set(1.0)
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.blend_set("NONE")
        except (AttributeError, RuntimeError, UnboundLocalError):
            pass


def label_lines() -> tuple[str, ...]:
    items = presentation_diagnostics()
    if not items and not _detected_count:
        return ("Solver Input",) if _show_input else ()
    parts = []
    if _detected_count:
        parts.append(
            f"{_detected_count} intersection"
            f"{'s' if _detected_count != 1 else ''}")
    if _degenerate_faces:
        count = len(_degenerate_faces)
        parts.append(
            f"{count} degenerate face{'s' if count != 1 else ''}")
    lines = ["Geometry Diagnostics", " · ".join(parts)]
    if _mapping_warning:
        lines.append(_mapping_warning)
    if _detail_notice:
        lines.append(_detail_notice)
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
