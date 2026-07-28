# SPDX-FileCopyrightText: 2026 Tim Christmann and Cloth NeXt contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bounded artist-facing Bake errors.

Technical diagnostics belong in ``failure.log`` and Blender's logs, never in
an artist-facing panel or the companion Bake window.  This module is the
single fail-closed boundary used by the controller and transport layer.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from ..core.error_codes import ERROR_CODES

UI_ERROR_SUMMARY_MAX_CHARS = 180
UI_ERROR_LINE_MAX_CHARS = 280
UI_ERROR_DETAILS_MAX_CHARS = 1400
UI_ERROR_MAX_LINES = 7

_ALLOWED_PREFIXES = (
    "Stage:", "Blender frame:", "Solver frame:", "Object:",
    "Cause:", "What to do:", "Diagnostic log:",
)

_TECHNICAL_MARKERS = re.compile(
    r"(?:traceback \(most recent call last\)|\n\s*file [\"']|"
    r"during handling of the above exception|the above exception was the direct|"
    r"---\s*solver log|stdout_tail\s*=|stderr_tail\s*=|progress_tail\s*=|"
    r"owned_process_id\s*=|0x[0-9a-f]{6,}|"
    r"\b(?:assertionerror|keyerror|typeerror|valueerror|runtimeerror|"
    r"oserror|filenotfounderror|permissionerror)\s*:)",
    re.IGNORECASE,
)

_PATH_MARKERS = re.compile(
    r"(?:[a-z]:\\|/(?:home|users|tmp|var|mnt)/|\\bl_ext\\|"
    r"\.py[\"']?,?\s+line\s+\d+)",
    re.IGNORECASE,
)

_ARTIST_CAUSES = {
    "CNX-E132": "The installed solver version is not compatible with this Cloth NeXt version",
    "CNX-E133": "The solver could not start correctly",
    "CNX-E134": "The solver stopped while starting",
    "CNX-E143": "The solver did not accept the exported scene",
    "CNX-E144": "The exported scene changed or was transferred incorrectly",
    "CNX-E145": "The solver returned an unreadable response",
    "CNX-E151": "The solver could not prepare this scene",
    "CNX-E153": "The solver could not build collisions for the scene",
    "CNX-E161": "The simulation could not find a stable solution",
    "CNX-E164": "The solver stopped during the simulation",
    "CNX-E165": "The simulation produced invalid vertex positions",
    "CNX-E169": "The solver entered an unexpected state",
    "CNX-E173": "The solver result could not be matched to the Blender objects",
    "CNX-E175": "A downloaded simulation frame is damaged",
    "CNX-E176": "The solver result no longer matches the object topology",
    "CNX-E182": "The playback cache could not be finalized",
    "CNX-E186": "The playback cache did not pass its integrity check",
    "CNX-E198": "The Bake worker stopped before returning a result",
    "CNX-E199": "Cloth NeXt encountered an unexpected internal error",
}

_ARTIST_ACTIONS = {
    "CNX-E134": (
        "Repair the solver installation and retry. Update the GPU driver only "
        "if the problem continues."),
    "CNX-E143": (
        "Run the solver health check, repair the installation if needed, then "
        "start a fresh Bake."),
    "CNX-E145": "Repair or update the solver installation, then retry.",
    "CNX-E151": (
        "Check the named geometry, Pins, and materials, then retry the Bake."),
    "CNX-E160": (
        "Check the reported frame and apply the suggested stability change."),
    "CNX-E164": (
        "Check the failing frame for intersections or extreme settings, then "
        "run the solver health check."),
    "CNX-E167": (
        "Retry after a successful solver health check. Keep the error code if "
        "the same frame fails again."),
    "CNX-E173": "Repair or update the solver installation, then Rebake.",
    "CNX-E174": (
        "Retry the Bake. If the same frame is missing again, report the error "
        "code and diagnostic log."),
    "CNX-E199": (
        "Retry once. If it happens again, report the error code and attach the "
        "diagnostic log."),
}


@dataclass(frozen=True, slots=True)
class ErrorPresentation:
    summary: str
    details: str


def _one_line(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def _clip(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 3)].rstrip(" ,;:-") + "..."


def _before_technical_section(value: str) -> str:
    match = _TECHNICAL_MARKERS.search(value)
    return value[:match.start()] if match else value


def _safe_free_text(value: object, *, limit: int = UI_ERROR_LINE_MAX_CHARS) -> str:
    text = _one_line(_before_technical_section(str(value or "")))
    if not text or _TECHNICAL_MARKERS.search(text) or _PATH_MARKERS.search(text):
        return ""
    if text.startswith(("{", "[")) and text.endswith(("}", "]")):
        return ""
    return _clip(text, limit)


def _prefixed_value(details: str, prefix: str) -> str:
    for raw in str(details or "").splitlines():
        line = raw.strip()
        if not line.startswith(prefix):
            continue
        value = line[len(prefix):].strip()
        if prefix == "Diagnostic log:":
            # A log path is useful recovery information and is the one path
            # deliberately allowed in the UI. It is still bounded to one line.
            return _clip(_one_line(value), UI_ERROR_LINE_MAX_CHARS)
        return _safe_free_text(value)
    return ""


def _simple_details_cause(details: str) -> str:
    raw = str(details or "").strip()
    if not raw or "\n" in raw or "\r" in raw:
        return ""
    return _safe_free_text(raw)


def _friendly_cause(error_code: str) -> str:
    info = ERROR_CODES.get(error_code)
    cause = _ARTIST_CAUSES.get(error_code)
    if cause:
        return cause
    return _safe_free_text(info.cause if info is not None else "")


def _friendly_action(error_code: str, fallback: str) -> str:
    action = _ARTIST_ACTIONS.get(error_code)
    if action:
        return action
    info = ERROR_CODES.get(error_code)
    candidate = info.action if info is not None else fallback
    safe = _safe_free_text(candidate)
    # Technical log vocabulary is fine inside the log, not as the primary
    # instruction shown to an artist.
    if re.search(r"\b(?:stderr|stdout|stack trace|traceback)\b", safe,
                 re.IGNORECASE):
        return "Retry the Bake. If it happens again, report the error code and diagnostic log."
    return safe or "Check the scene settings, then retry the Bake."


def _friendly_stage(error_code: str, fallback: str) -> str:
    explicit = _safe_free_text(fallback, limit=120)
    if explicit:
        return explicit
    info = ERROR_CODES.get(error_code)
    return _safe_free_text(info.stage if info is not None else "Bake", limit=120) or "Bake"


def _friendly_summary(summary: str, error_code: str) -> str:
    candidate = _safe_free_text(summary, limit=UI_ERROR_SUMMARY_MAX_CHARS)
    if candidate and not re.search(
            r"(?:failed unexpectedly|unexpected.*failure|reported a failure|"
            r"error occurred|exception)", candidate, re.IGNORECASE):
        return candidate
    cause = _friendly_cause(error_code)
    if cause:
        return _clip(cause.rstrip(".") + ".", UI_ERROR_SUMMARY_MAX_CHARS)
    return "The Bake could not be completed."


def _bounded_lines(lines: list[str]) -> str:
    clean: list[str] = []
    for line in lines:
        line = _clip(_one_line(line), UI_ERROR_LINE_MAX_CHARS)
        if line and line not in clean:
            clean.append(line)
        if len(clean) >= UI_ERROR_MAX_LINES:
            break
    while clean and len("\n".join(clean)) > UI_ERROR_DETAILS_MAX_CHARS:
        clean.pop()
    return "\n".join(clean)


def present_error(summary: str, details: str = "", *, error_code: str = "",
                  stage: str = "", action: str = "") -> ErrorPresentation:
    """Return a deterministic artist-facing error with no raw diagnostics."""
    code = str(error_code or "").strip().upper()
    stage_value = _prefixed_value(details, "Stage:") or _friendly_stage(
        code, stage)
    cause = (_prefixed_value(details, "Cause:")
             or _simple_details_cause(details)
             or _friendly_cause(code)
             or "The Bake could not complete this step")
    action_value = (_prefixed_value(details, "What to do:")
                    or _prefixed_value(details, "Recommended:")
                    or _friendly_action(code, action))

    lines = [f"Stage: {stage_value}"]
    for prefix in ("Blender frame:", "Solver frame:", "Object:"):
        value = _prefixed_value(details, prefix)
        if value:
            lines.append(f"{prefix} {value}")
    lines.append(f"Cause: {cause.rstrip('.') }.")
    lines.append(f"What to do: {action_value}")
    log_path = _prefixed_value(details, "Diagnostic log:")
    if log_path:
        lines.append(f"Diagnostic log: {log_path}")
    return ErrorPresentation(
        _friendly_summary(summary, code), _bounded_lines(lines))


def sanitize_transport_error(summary: str, details: str, error_code: str) \
        -> ErrorPresentation:
    """Defense-in-depth for snapshots not produced by BakeController.fail."""
    return present_error(summary, details, error_code=error_code)


def compact_detail_lines(details: str, *, limit: int = 3) -> tuple[str, ...]:
    """Return the highest-value bounded rows for compact UI surfaces."""
    values = []
    for prefix in ("Stage:", "Blender frame:", "What to do:", "Cause:",
                   "Diagnostic log:"):
        value = _prefixed_value(details, prefix)
        if value:
            values.append(f"{prefix} {value}")
        if len(values) >= max(1, int(limit)):
            break
    return tuple(values)
